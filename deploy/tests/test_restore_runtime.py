"""验证执行顺序、恢复暂存清理与停服边界；Docker 使用协议替身。"""

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import restore_runtime as rt

CONTAINER = "c" * 64


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        (self.root / "secrets").mkdir()
        (self.root / "data").mkdir()
        for name in (".env", "dify.env"):
            rt.ws.write(
                self.root / "secrets" / name,
                "EB_DOMAIN=recovery.localhost\nEB_FRAPPE_DB_PASSWORD=synthetic-root\nEB_FRAPPE_ADMIN_PASSWORD=synthetic-admin\n",
            )
        self.manifest: dict[str, Any] = {"images": {}}
        self.calls = []
        for stack, suffix, names in (
            ("edge", "", ["caddy", "wechat"]),
            ("dify", "-dify", ["api", "db_postgres", "redis", "qdrant", "worker"]),
            (
                "hr",
                "-hr",
                [
                    "backend",
                    "frontend",
                    "db",
                    "redis-cache",
                    "redis-queue",
                    "scheduler",
                    "configurator",
                ],
            ),
        ):
            services: dict[str, Any] = {name: {} for name in names}
            rt.ws.write(
                self.root / f"data/restore-{stack}.json",
                json.dumps(dict(name="recovery" + suffix, services=services)),
            )
            self.manifest["images"].update(
                {
                    suffix + "/" + name: {"image_id": "sha256:" + "a" * 64}
                    for name in names
                    if name != "configurator"
                }
            )
        self.target = rt.Target(self.root, "recovery", self.run_command)

    def run_command(self, args, source=None, env=None):
        self.calls.append((args, source, env))
        if args[1] == "ps":
            self.assertTrue(any(rt.MARK + "=" + self.target.operation in a for a in args))
            self.assertIn("--no-trunc", args)
            return CONTAINER.encode()
        return b""

    def test_data_restores_before_app_start_and_no_tasks_or_wechat_start(self):
        phases = []
        with ExitStack() as patches:
            for module, name in ((rt.pg, "pg"), (rt.qd, "qdrant"), (rt.storage, "storage")):

                def component(*args, name=name, **kwargs):
                    self.assertFalse(any("-d" in c[0] and "backend" in c[0] for c in self.calls))
                    phases.append(name)
                    kwargs["run"](["docker", "stdin-probe"], self.root / "payload")

                patches.enter_context(patch.object(module, "restore", side_effect=component))
            patches.enter_context(patch.object(rt, "verify"))
            self.target.restore(self.manifest, self.root, ["hr.source"], "hr.source", 18080)
        self.assertEqual(phases, ["pg", "qdrant", "storage"])
        started = [c[0] for c in self.calls if "-d" in c[0]]
        self.assertTrue(started and "caddy" in started[-1])
        self.assertFalse(any(set(c) & rt.HOLD for c in started))
        credentialed = [(c, env) for c, _, env in self.calls if "EB_RESTORE_ADMIN_PASSWORD" in c]
        self.assertTrue(credentialed)
        self.assertTrue(
            all(
                "synthetic-admin" not in c and env["EB_RESTORE_ADMIN_PASSWORD"] == "synthetic-admin"
                for c, env in credentialed
            )
        )
        self.assertIn(
            ["docker", "exec", "--user", "root", CONTAINER, "chown", "-R", "frappe:"],
            [c[0][:8] for c in self.calls],
        )
        self.assertIn(
            [
                "docker",
                "exec",
                "--user",
                "root",
                CONTAINER,
                "rm",
                "-rf",
                "/tmp/eb-restore-" + self.target.operation,
            ],
            [c[0] for c in self.calls],
        )

    def test_native_failure_and_cleanup_failure_preserve_error_and_stop_all_projects(self):
        original = self.target.run

        def failed(args, source=None, env=None):
            if "restore_frappe.py" in " ".join(args) and "env/bin/python" in args:
                raise OSError("synthetic private payload")
            if args[1] == "exec" and "rm" in args:
                raise RuntimeError("cleanup")
            return original(args, source, env)

        self.target.run, self.target.step = failed, "Frappe"
        with self.assertRaisesRegex(rt.CollectionError, "^Frappe:OSError$"):
            self.target.frappe(self.root, ["hr.source"], "hr.source", 18080)
        self.assertIn(["docker", "rm", "-f", CONTAINER], [c[0] for c in self.calls])
        self.assertEqual(self.target.stop(), [])
        self.assertEqual(sum(c[0][1] == "stop" for c in self.calls), 3)
