"""真实锁和私有配置下的总入口测试；不接真实Docker或机器人。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import backup
import health


class EntryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        (self.root / "secrets").mkdir(mode=0o700)
        self.env = self.root / "secrets/.env"
        self.env.write_text("EB_PROJECT=eb\nEB_DOMAIN=example.invalid\nEB_SCHEME=http\n")
        self.env.chmod(0o600)
        for module in (health, backup):
            p = patch.object(module, "ROOT", self.root)
            p.start()
            self.addCleanup(p.stop)

    def test_no_webhook_failed_checks_exit_normally_with_private_state(self):
        with (
            patch.object(
                health.health_services,
                "inventory",
                return_value=({}, {"container.hr.backend": (False, 0)}),
            ),
            patch.object(health.health_services, "check", return_value={"scheduler": (False, 0)}),
            patch.object(health.health_metrics, "check", return_value={"disk": (False, 1)}),
            patch.object(health.health_alerts, "send") as sender,
            redirect_stdout(io.StringIO()) as log,
        ):
            self.assertEqual(health.main([str(self.env)]), 0)
            self.assertEqual(health.main([str(self.env)]), 0)
        sender.assert_not_called()
        text = log.getvalue()
        self.assertIn("health:disk:失败:1.00", text)
        self.assertNotIn("example.invalid", text)
        state = self.root / "data/health-state.json"
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            set(json.loads(state.read_text())["issues"]),
            {"container.hr.backend", "scheduler", "disk"},
        )

    def test_maintenance_skips_and_unsafe_webhook_or_permissions_refused(self):
        with (
            backup.operation(),
            patch.object(health.health_services, "inventory") as probe,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(health.main([str(self.env)]), 0)
        probe.assert_not_called()
        self.assertFalse((self.root / "data/health-state.json").exists())
        base = self.env.read_text()
        for hook in (
            "http://private.invalid/key",
            "https://user:private@host.invalid/key",
            "https://:private@host.invalid/key",
        ):
            self.env.write_text(base + "EB_ALERT_WECOM_WEBHOOK=" + hook + "\n")
            with (
                redirect_stderr(io.StringIO()) as log,
                patch.object(health.health_services, "inventory") as probe,
            ):
                self.assertEqual(health.main([str(self.env)]), 1)
            probe.assert_not_called()
            self.assertNotIn("private", log.getvalue())
        self.env.chmod(0o644)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(health.main([str(self.env)]), 1)
