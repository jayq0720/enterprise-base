"""容器缺失/不健康、HTTP超时、调度锁和队列的脱敏协议测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import health_services as service


class ServiceTests(unittest.TestCase):
    def test_inventory_checks_expected_missing_health_oneoff_and_profiles(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "secrets").mkdir()
            (root / "secrets/dify.env").write_text("SECRET_KEY=private\n")

            projects = iter(("eb", "eb-dify", "eb-hr"))

            def run(args, **kwargs):
                if "config" in args:
                    stack = (
                        "hr"
                        if "frappe" in args[args.index("-f") + 1]
                        else "dify"
                        if "dify/docker" in args[args.index("-f") + 1]
                        else "edge"
                    )
                    return json.dumps(
                        dict(
                            name="eb" + {"hr": "-hr", "dify": "-dify", "edge": ""}[stack],
                            services={
                                "backend": dict(healthcheck=dict(test=["CMD", "true"])),
                                "missing": {},
                                "wechat": dict(profiles=["wechat"]),
                                "configurator": {},
                            },
                        )
                    ).encode()
                if "ps" in args:
                    return b"id"
                project = next(projects)
                records = [
                    dict(
                        Id="x",
                        Config=dict(
                            Labels={
                                service.LABEL + "project": project,
                                service.LABEL + "service": name,
                            }
                        ),
                        State=state,
                    )
                    for name, state in (
                        ("backend", dict(Running=True, Health=dict(Status="unhealthy"))),
                        ("configurator", dict(Status="exited", ExitCode=0)),
                    )
                ]
                return json.dumps(records).encode()

            _, result = service.inventory(root, dict(EB_PROJECT="eb"), set(), run)
            self.assertEqual(result["container.hr.missing"], (False, 0))
            self.assertEqual(result["container.hr.backend"], (False, 0))
            self.assertEqual(result["container.hr.configurator"], (True, 1))
            self.assertNotIn("container.hr.wechat", result)
            with self.assertRaises(ValueError):
                service.inventory(root, dict(EB_PROJECT="eb"), {"unknown"}, run)

    def test_http_and_scheduler_in_scheduler_container_queue_boundary(self):
        containers = {
            (stack, name): dict(Id=name + "-id")
            for stack, name in [
                ("dify", "api"),
                ("hr", "backend"),
                ("hr", "scheduler"),
                ("hr", "frappe-mcp"),
            ]
        }
        run = Mock(side_effect=[b"200", b"200", b"200", b"[true,100]"])
        result = service.check(
            containers, dict(EB_DOMAIN="example.invalid"), dict(HTTP_TIMEOUT=10, QUEUE_MAX=100), run
        )
        self.assertTrue(all(ok for ok, _ in result.values()))
        self.assertIn("scheduler-id", run.call_args.args[0])
        self.assertNotIn("backend-id", run.call_args.args[0])
        run.side_effect = [b"503", TimeoutError("employee secret"), b"403", b"[false,101]"]
        result = service.check(
            containers, dict(EB_DOMAIN="example.invalid"), dict(HTTP_TIMEOUT=10, QUEUE_MAX=100), run
        )
        self.assertTrue(all(not ok for ok, _ in result.values()))
        self.assertNotIn("employee", json.dumps(result))
        self.assertNotIn("example.invalid", json.dumps(result))
        result = service.check(
            {}, dict(EB_DOMAIN="example.invalid"), dict(HTTP_TIMEOUT=10, QUEUE_MAX=100), run
        )
        self.assertEqual(result["scheduler"], (False, None))
