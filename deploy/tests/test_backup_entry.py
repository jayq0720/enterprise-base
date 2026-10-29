"""入口用真实 age 和真实进程锁；Docker 原生命令沿用协议替身。"""

import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import backup
import backup_bundle as bundle
from backup_collect import Collector
from test_backup_collect import Docker


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(
            self.stack.enter_context(
                tempfile.TemporaryDirectory(
                    dir="/private/tmp" if Path("/private/tmp").exists() else None
                )
            )
        )
        self.identity = self.root / "identity"
        keygen = bundle.tool("age-keygen")
        subprocess.run([keygen, "-o", str(self.identity)], check=True, stderr=subprocess.DEVNULL)
        recipient = subprocess.check_output([keygen, "-y", str(self.identity)], text=True).strip()
        for name in ("apps.lock.json", "age.lock.json"):
            (self.root / name).write_bytes((backup.ROOT / name).read_bytes())
        secrets = self.root / "secrets"
        secrets.mkdir(mode=0o700)
        self.env = secrets / ".env"
        self.env.write_text("EB_PROJECT=customer\nEB_BACKUP_RECIPIENT=" + recipient + "\n")
        self.env.chmod(0o600)
        (secrets / "dify.env").write_text("SYNTHETIC=private-content\n")
        (secrets / "dify.env").chmod(0o600)
        self.stack.enter_context(patch.object(backup, "ROOT", self.root))
        self.stack.enter_context(
            patch.object(backup.subprocess, "check_output", return_value="a" * 40)
        )
        self.stack.enter_context(patch.object(backup, "Collector", side_effect=self.collector))
        self.docker = Docker()

    def collector(self, project):
        return Collector(project, run=self.docker, sleep=lambda _: None)

    def invoke(self):
        output = io.StringIO()
        with (
            patch.object(backup.sys, "argv", ["backup.py", str(self.env)]),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            code = backup.main()
        self.assertNotIn("private-content", output.getvalue())
        self.assertNotIn("must-not-leak", output.getvalue())
        self.assertFalse(list((self.root / "data/backups").glob(".backup-clear-*")))
        return code, output.getvalue()

    def test_success_encrypts_secrets_certificate_session_and_manifest(self):
        folder = self.root / "data/backups"
        folder.mkdir(mode=0o700, parents=True)
        expired = folder / bundle.backup_name("customer", datetime.now(UTC) - timedelta(days=90))
        expired.touch()
        self.assertEqual(self.invoke(), (0, "备份:成功\n"))
        self.assertFalse(expired.exists())
        archives = list((self.root / "data/backups").glob("*.tar.age"))
        self.assertEqual(len(archives), 1)
        self.assertNotIn(b"private-content", archives[0].read_bytes())
        manifest = bundle.unpack(archives[0], self.root / "restored", self.identity)
        self.assertEqual(manifest["project"], "customer")
        paths = {row["path"] for row in manifest["files"]}
        required = {
            "secrets/.env",
            "secrets/dify.env",
            "payload/caddy.tar",
            "payload/wechat.tar",
            "payload/frappe.tar",
        }
        self.assertLessEqual(required, paths)
        self.assertEqual(
            (self.root / "restored/secrets/dify.env").read_text(), "SYNTHETIC=private-content\n"
        )

    def test_collect_encrypt_and_secret_copy_failures_clear_plaintext_without_pruning(self):
        for stage in ("collect", "encrypt", "secrets"):
            with self.subTest(stage=stage), patch.object(bundle, "prune") as prune:
                self.docker = Docker("pg_dump -U" if stage == "collect" else "")
                target = backup if stage == "secrets" else bundle
                function = "copy_secrets" if stage == "secrets" else "pack"
                with (
                    patch.object(target, function, side_effect=OSError("private-content"))
                    if stage != "collect"
                    else ExitStack()
                ):
                    self.assertEqual(self.invoke()[0], 1)
                prune.assert_not_called()

    def test_invalid_recipient_permissions_and_links_fail_before_stopping(self):
        for case in ("recipient", "permissions", "link"):
            with self.subTest(case=case):
                original = self.env.read_text()
                if case == "recipient":
                    self.env.write_text(original.replace("age1", "invalid1"))
                elif case == "permissions":
                    self.env.chmod(0o644)
                else:
                    (self.root / "secrets/link").symlink_to(self.identity)
                self.assertEqual(self.invoke()[0], 1)
                self.assertFalse(any(c[0] == "stop" for c in self.docker.calls))
                self.env.write_text(original)
                self.env.chmod(0o600)

    def test_real_process_lock_refuses_second_operation(self):
        with backup.operation():
            program = (
                "import sys;sys.path.insert(0,sys.argv[1]);import backup;"
                "from pathlib import Path;backup.ROOT=Path(sys.argv[2]);"
                "backup.operation().__enter__()"
            )
            result = subprocess.run(
                [sys.executable, "-c", program, str(Path(backup.__file__).parent), str(self.root)],
                capture_output=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"BlockingIOError", result.stderr)

    def test_lock_wait_retries_until_short_holder_releases(self):
        held, release = threading.Event(), threading.Event()

        def holder():
            with backup.operation():
                held.set()
                release.wait(5)

        thread = threading.Thread(target=holder)
        thread.start()
        held.wait(5)
        with self.assertRaises(BlockingIOError):
            with backup.operation():
                pass
        threading.Timer(1.5, release.set).start()
        started = time.monotonic()
        with backup.operation(wait=10):
            waited = time.monotonic() - started
        thread.join()
        self.assertGreaterEqual(waited, 1)
        self.assertLess(waited, 10)

    def test_termination_defers_until_collector_finishes_then_cleans(self):
        def collect(_folder):
            os.kill(os.getpid(), signal.SIGTERM)
            return {}

        with patch.object(backup, "Collector") as factory, patch.object(bundle, "prune") as prune:
            factory.return_value.collect.side_effect = collect
            self.assertEqual(self.invoke(), (1, "备份:失败:InterruptedError\n"))
            prune.assert_not_called()

    def test_release_owned_lock_is_reused_for_real_encrypted_backup(self):
        with (
            backup.operation(),
            patch.object(backup, "operation", side_effect=AssertionError("不能获取第二把锁")),
        ):
            archive = backup.backup(self.env, locked=True)
        self.assertTrue(archive.is_file())
        self.assertFalse(list((self.root / "data/backups").glob(".backup-clear-*")))
        manifest = bundle.unpack(archive, self.root / "from-release", self.identity)
        self.assertEqual(manifest["project"], "customer")
