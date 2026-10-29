"""原生现有站点回退的凭据隔离、附件及故障测试；真实SQL回退由CI验证。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import restore_frappe_existing as existing


class ExistingTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name).resolve()
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.input = self.root / "input"
        self.input.mkdir(mode=0o700)
        for name in existing.SITE_FILES:
            (self.input / name).write_bytes(b"synthetic snapshot")
            (self.input / name).chmod(0o600)
        self.original = dict(
            db_name="old", db_password="old-password", encryption_key="snapshot-key"
        )
        (self.input / "site_config.json").write_text(json.dumps(self.original))
        self.target = self.root / "sites/recovery.test"
        self.target.mkdir(parents=True)
        self.config = self.target / "site_config.json"
        self.current = dict(
            db_name="current",
            db_password="current-password",
            db_host="db",
            eb_wecom_cards=dict(enabled=True, token="private-current-token"),
        )
        self.config.write_text(json.dumps(self.current))
        for kind in ("public", "private"):
            folder = self.target / kind / "files"
            folder.mkdir(parents=True)
            (folder / "post-release.txt").write_text("synthetic new content")
        self.calls, self.fault = [], ""

    def command(self, args):
        self.calls.append(args)
        current = json.loads(self.config.read_text())
        self.assertEqual(current["db_name"], "current")
        self.assertTrue(current["pause_scheduler"] and current["mute_emails"])
        self.assertFalse(current["eb_wecom_cards"]["enabled"])
        self.assertTrue(
            all(
                not (self.target / k / "files/post-release.txt").exists()
                for k in ("public", "private")
            )
        )
        if "restore" in args:
            for name in ("files.tar", "private-files.tar"):
                (self.target / name).write_text("synthetic temporary tar")
        if self.fault in args:
            raise OSError("private data must not leak")

    def restore(self):
        existing.restore("recovery.test", self.input, "root", "admin", self.command)

    def test_keep_target_database_only_copy_decryption_keys_and_resume_target_settings(self):
        self.restore()
        result = json.loads(self.config.read_text())
        for key in self.current:
            self.assertEqual(result[key], self.current[key])
        self.assertEqual(result["encryption_key"], "snapshot-key")
        self.assertEqual((result["pause_scheduler"], result["mute_emails"]), (0, 0))
        self.assertFalse(list(self.target.glob("*.tar")))
        self.assertEqual([c[3] for c in self.calls], ["restore", "migrate", "set-admin-password"])
        self.assertIn("--force", self.calls[0])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_restore_failure_keeps_notifications_paused_and_removes_temporary_archives(self):
        self.fault = "restore"
        with self.assertRaisesRegex(existing.RestoreError, "^Frappe回滚:数据库:OSError$"):
            self.restore()
        result = json.loads(self.config.read_text())
        self.assertTrue(result["pause_scheduler"] and result["mute_emails"])
        self.assertFalse(result["eb_wecom_cards"]["enabled"])
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(list(self.target.glob("*.tar")))

    def test_untrusted_input_and_missing_target_refused_before_removing_attachments(self):
        (self.input / "files.tar").chmod(0o644)
        with self.assertRaises(ValueError):
            self.restore()
        self.assertTrue((self.target / "public/files/post-release.txt").exists())
        (self.input / "files.tar").chmod(0o600)
        self.config.unlink()
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse(self.calls)
