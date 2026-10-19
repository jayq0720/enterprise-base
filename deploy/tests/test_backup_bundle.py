"""用真实 age 验证加密包及恶意输入边界，不访问生产数据。"""

import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import backup_bundle as bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.identity = Path(self.work.name) / "identity"
        self.keygen = bundle.tool("age-keygen")
        subprocess.run(
            [self.keygen, "-o", str(self.identity)], check=True, stderr=subprocess.DEVNULL
        )
        self.recipient = subprocess.check_output(
            [self.keygen, "-y", str(self.identity)], text=True
        ).strip()
        self.meta = dict(project="test", revision="a" * 40, images={}, versions={"age": "1.2.1"})

    def tearDown(self):
        self.work.cleanup()

    def test_roundtrip_permissions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "secrets.env").write_bytes(b"SYNTHETIC_SECRET_FOR_BACKUP_TEST")
            archive, dest = root / "backup.age", root / "restored"
            expected = bundle.pack(source, archive, self.recipient, self.meta)
            self.assertNotIn(b"SYNTHETIC_SECRET_FOR_BACKUP_TEST", archive.read_bytes())
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            actual = bundle.unpack(archive, dest, self.identity)
            self.assertEqual(actual, expected)
            self.assertTrue(actual["created_at"].endswith("+08:00"))
            self.assertEqual(
                (dest / "secrets.env").read_bytes(),
                (source / "secrets.env").read_bytes(),
            )
            self.assertEqual(dest.stat().st_mode & 0o777, 0o700)
            self.assertEqual((dest / "secrets.env").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                bundle.pack(source, archive, self.recipient, self.meta)
            with self.assertRaises(ValueError):
                bundle.unpack(archive, dest, self.identity)
            wrong = root / "wrong-identity"
            subprocess.run([self.keygen, "-o", str(wrong)], check=True, stderr=subprocess.DEVNULL)
            with self.assertRaises(subprocess.CalledProcessError):
                bundle.unpack(archive, root / "wrong", wrong)
            raw = archive.read_bytes()
            archive.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
            with self.assertRaises(subprocess.CalledProcessError):
                bundle.unpack(archive, root / "corrupt", self.identity)
            self.assertFalse((root / "corrupt").exists())

    def test_reject_source_links_reserved_names_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "link").symlink_to(self.identity)
            with self.assertRaises(ValueError):
                bundle.pack(source, root / "bad.age", self.recipient, self.meta)
            (source / "link").unlink()
            (source / "manifest.json").write_text("{}")
            with self.assertRaises(ValueError):
                bundle.pack(source, root / "bad.age", self.recipient, self.meta)
            with self.assertRaises(ValueError):
                bundle.pack(
                    source,
                    root / "bad.age",
                    self.recipient,
                    {**self.meta, "secret": "synthetic"},
                )

    def test_authenticated_but_unsafe_tar_is_rejected_before_writing(self):
        for attack in ("traversal", "symlink", "duplicate", "checksum"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as folder:
                root, raw = Path(folder), io.BytesIO()
                with tarfile.open(fileobj=raw, mode="w") as tar:
                    info = tarfile.TarInfo("../escape" if attack == "traversal" else "payload")
                    if attack == "symlink":
                        info.type, info.linkname = tarfile.SYMTYPE, "../escape"
                        tar.addfile(info)
                    else:
                        info.size = 1
                        tar.addfile(info, io.BytesIO(b"x"))
                        if attack == "duplicate":
                            tar.addfile(info, io.BytesIO(b"x"))
                    content = json.dumps(
                        dict(
                            schema=1,
                            files=[dict(path="payload", size=1, sha256="0" * 64)],
                        )
                    ).encode()
                    info = tarfile.TarInfo("manifest.json")
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
                archive = root / "bad.age"
                with archive.open("wb") as target:
                    subprocess.run(
                        [bundle.tool("age"), "-r", self.recipient],
                        input=raw.getvalue(),
                        stdout=target,
                        stderr=subprocess.DEVNULL,
                        check=True,
                    )
                with self.assertRaises(ValueError):
                    bundle.unpack(archive, root / "restored", self.identity)
                self.assertFalse((root / "restored").exists())
                self.assertFalse((root / "escape").exists())

    def test_retention_keeps_daily_and_four_week_slots_only(self):
        now = datetime(2026, 10, 19, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as folder:
            root, samples = Path(folder), {}
            for day in range(45):
                path = root / bundle.backup_name("test", now - timedelta(days=day, hours=1))
                path.touch()
                samples[day] = path
            foreign = root / bundle.backup_name("other", now - timedelta(days=99))
            foreign.touch()
            link = root / bundle.backup_name("test", now - timedelta(days=100))
            link.symlink_to(foreign)
            bundle.prune(root, "test", now)
            self.assertEqual({d for d, p in samples.items() if p.exists()}, {*range(7), 8, 15})
            self.assertTrue(foreign.exists() and link.is_symlink())

    def test_bad_official_download_is_not_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "age.lock.json").write_bytes((bundle.ROOT / "age.lock.json").read_bytes())
            with (
                patch.object(bundle, "ROOT", root),
                patch.object(bundle.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")),
            ):
                with self.assertRaises(ValueError):
                    bundle.tool("age")
