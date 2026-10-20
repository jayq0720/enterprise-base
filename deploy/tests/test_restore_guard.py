"""验证恢复隔离、白名单和原生归档；不连接真实 Docker 或业务系统。"""

import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backup_bundle as bundle
import restore_guard as guard


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name).resolve()
        self.target = self.root / "new-project"
        self.calls = []

    def empty(self, args):
        self.calls.append(args)
        return b""

    def test_new_project_rejects_overlap_existing_paths_labels_and_unlabelled_volumes(self):
        for project in ("source", "source-hr", "source-dify", "invalid/project"):
            with self.assertRaises(ValueError):
                guard.new_project(project, "source", self.target, self.empty)
        self.assertFalse(self.calls)
        guard.new_project("recovery", "source", self.target, self.empty)
        self.assertTrue(all(c[1] in {"ps", "volume", "network"} for c in self.calls))
        for command in ("ps", "volume", "network"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                guard.new_project(
                    "recovery",
                    "source",
                    self.target,
                    lambda args, command=command: b"existing" if args[1] == command else b"",
                )
        for existing in ("recovery_db", "recovery-hr_sites", "recovery-dify_data", "recovery_edge"):
            with self.subTest(name=existing), self.assertRaises(ValueError):
                guard.new_project(
                    "recovery",
                    "source",
                    self.target,
                    lambda args, item=existing: item.encode() if "--format" in args else b"",
                )
        self.target.mkdir()
        with self.assertRaises(ValueError):
            guard.new_project("recovery", "source", self.target, self.empty)
        link = self.root / "link"
        link.symlink_to(self.target)
        with self.assertRaises(ValueError):
            guard.new_project("recovery", "source", link / "child", self.empty)

    def test_only_decryption_keys_cross_site_boundary(self):
        original = dict(
            db_name="original-db",
            db_password="synthetic-original",
            db_host="original-host",
            encryption_key="synthetic-encryption",
            backup_encryption_key="synthetic-backup",
            host_name="https://original.invalid",
            eb_wecom_cards={"enabled": True},
            arbitrary="must-not-import",
        )
        target = dict(db_name="new-db", db_password="synthetic-new", db_host="new-host")
        result = guard.site_settings(original, target)
        for key in target:
            self.assertEqual(result[key], target[key])
        self.assertEqual(result["encryption_key"], original["encryption_key"])
        self.assertEqual(result["backup_encryption_key"], original["backup_encryption_key"])
        self.assertFalse(result["eb_wecom_cards"]["enabled"])
        self.assertEqual((result["pause_scheduler"], result["mute_emails"]), (1, 1))
        self.assertNotIn("arbitrary", result)
        self.assertNotIn("host_name", result)
        self.assertNotIn("encryption_key", target)
        with self.assertRaises(ValueError):
            guard.site_settings(original, {"db_name": "not-created"})

    def native_archive(self, defect=None):
        archive = self.root / "frappe.tar"
        with tarfile.open(archive, "w") as output:
            folder = tarfile.TarInfo("./0")
            folder.type = tarfile.DIRTYPE
            output.addfile(folder)
            for filename in sorted(guard.SITE_FILES):
                if defect == "missing" and filename == "files.tar":
                    continue
                info = tarfile.TarInfo("./0/" + filename)
                if defect in {"symlink", "hardlink"} and filename == "files.tar":
                    info.type = tarfile.SYMTYPE if defect == "symlink" else tarfile.LNKTYPE
                    info.linkname = "../escape"
                    output.addfile(info)
                    continue
                data = b"synthetic-native-payload"
                if filename == "site_config.json":
                    data = b'{"encryption_key":"synthetic"}'
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))
            if defect and defect not in {"missing", "symlink", "hardlink"}:
                info = tarfile.TarInfo(
                    {
                        "traversal": "../escape",
                        "absolute": "/escape",
                        "duplicate": "0/files.tar",
                    }.get(defect, "0/unknown")
                )
                output.addfile(info)
        return archive

    def test_native_frappe_archive_is_checked_before_writing(self):
        for defect in ("missing", "traversal", "absolute", "duplicate", "symlink", "hardlink"):
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                guard.extract_sites(self.native_archive(defect), self.target, ["hr.test"])
            self.assertFalse(self.target.exists())
        guard.extract_sites(self.native_archive(), self.target, ["hr.test"])
        self.assertEqual({p.name for p in (self.target / "0").iterdir()}, guard.SITE_FILES)
        self.assertTrue(
            all(p.stat().st_mode & 0o777 == 0o600 for p in (self.target / "0").iterdir())
        )
        with self.assertRaises(ValueError):
            guard.extract_sites(self.native_archive(), self.target, ["hr.test"])

    def packet(self):
        folder = self.root / "packet"
        payload = folder / "payload"
        payload.mkdir(parents=True)
        (folder / "secrets").mkdir()
        (folder / "secrets/.env").write_text("SYNTHETIC=configuration")
        names = (
            "frappe.tar api.tar plugin_daemon.tar caddy.tar qdrant.snapshot "
            "postgres-globals.sql postgres-0.dump wechat.tar"
        ).split()
        for name in names:
            (payload / name).write_bytes(b"synthetic")
        (payload / "sites.json").write_text(json.dumps(["hr.test"]))
        (payload / "databases.json").write_text(json.dumps(["dify"]))
        (payload / "frappe.tar").write_bytes(self.native_archive().read_bytes())
        return folder, dict(
            project="source", revision="a" * 40, images={"/wechat": {}}, versions={}
        )

    def test_component_layout_catches_missing_certificate_session_dump_and_bad_indexes(self):
        folder, manifest = self.packet()
        payload = folder / "payload"
        self.assertEqual(guard.layout(folder, manifest), (["hr.test"], ["dify"]))
        for name in ("caddy.tar", "wechat.tar", "postgres-0.dump"):
            (payload / name).unlink()
            with self.assertRaises(ValueError):
                guard.layout(folder, manifest)
            (payload / name).write_bytes(b"synthetic")
        for sites in (["hr.test", "hr.test"], ["../escape"], []):
            (payload / "sites.json").write_text(json.dumps(sites))
            with self.assertRaises(ValueError):
                guard.layout(folder, manifest)
        (payload / "sites.json").write_text('["hr.test"]')
        (payload / "databases.json").write_text('["host=outside.invalid dbname=main"]')
        with self.assertRaises(ValueError):
            guard.layout(folder, manifest)

    def test_real_decryption_cleanup_and_tamper_refusal_before_docker(self):
        folder, metadata = self.packet()
        identity, archive = self.root / "identity", self.root / "backup.age"
        keygen = bundle.tool("age-keygen")
        subprocess.run([keygen, "-o", str(identity)], check=True, stderr=subprocess.DEVNULL)
        recipient = subprocess.check_output([keygen, "-y", str(identity)], text=True).strip()
        bundle.pack(folder, archive, recipient, metadata)
        with patch.object(guard.backup, "ROOT", self.root):
            for failed in (False, True):
                entered = False
                try:
                    with guard.prepared(
                        archive, identity, "recovery", self.target, self.empty
                    ) as data:
                        entered = True
                        manifest, stage, sites, databases = data
                        self.assertEqual((sites, databases), (["hr.test"], ["dify"]))
                        self.assertEqual(manifest["project"], "source")
                        self.assertTrue((stage / "sites/0/site_config.json").is_file())
                        if failed:
                            raise OSError("synthetic failure")
                except OSError:
                    self.assertTrue(failed)
                self.assertTrue(entered)
                self.assertFalse(list((self.root / "data").glob(".restore-clear-*")))
                self.assertFalse(self.target.exists())
            raw = archive.read_bytes()
            archive.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
            self.calls.clear()
            with (
                self.assertRaises(subprocess.CalledProcessError),
                guard.prepared(archive, identity, "recovery", self.target, self.empty),
            ):
                self.fail("篡改包不能进入恢复阶段")
            self.assertFalse(self.calls)
            self.assertFalse(list((self.root / "data").glob(".restore-clear-*")))
