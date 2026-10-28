"""发布顺序和故障边界的 Docker 协议测试；镜像实际构建由原生CI完成。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import release_runtime as rt
from backup_collect import CollectionError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        (self.root / "secrets").mkdir()
        (self.root / "data").mkdir()
        for name in (".env", "dify.env"):
            rt.write(
                self.root / "secrets" / name,
                "EB_PROJECT=synthetic\nEB_DOMAIN=synthetic.invalid\nSECRET_KEY=private-sentinel\n",
            )
        self.models: dict[str, Any] = {}
        self.active: set[str] = set()
        self.previous: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.image = "sha256:" + "b" * 64
        for stack, suffix, services in (
            ("edge", "", ["caddy", "wechat"]),
            ("dify", "-dify", ["api", "web", "db_postgres", "redis", "qdrant", "worker"]),
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
                    "frappe-mcp",
                    "policy-sync",
                ],
            ),
        ):
            self.models[stack] = dict(
                name="synthetic" + suffix,
                services={n: dict(image="official:1.2.3") for n in services},
            )
            self.active.update(suffix + "/" + n for n in services)
            self.previous.update({suffix + "/" + n: self.image for n in services})
        self.models["hr"]["services"]["configurator"] = dict(image="missing:9.9.9")
        self.fault = ""

    def execute(self, args, source=None, env=None):
        self.calls.append(args)
        if self.fault and self.fault in args:
            raise OSError("private-sentinel")
        if args[1:3] == ["image", "inspect"]:
            if "missing:9.9.9" in args:
                raise RuntimeError("stale configurator")
            return self.image.encode()
        if "config" in args:
            filename = args[args.index("-f") + 1]
            stack = "hr" if "/frappe/" in filename else "dify" if "/dify/" in filename else "edge"
            self.assertNotIn("SECRET_KEY", env)
            return json.dumps(self.models[stack]).encode()
        if args[1] == "ps":
            return ("c" * 64).encode()
        return b"200"

    def runtime(self):
        return rt.Runtime(self.root, "synthetic", self.active, self.previous, self.execute)

    def test_preflight_resolves_configurator_before_pull_and_preserves_disabled_services(self):
        self.active.remove("/wechat")
        runtime = self.runtime()
        self.assertNotIn("wechat", runtime.selected["edge"])
        self.assertFalse(any("missing:9.9.9" in c for c in self.calls))
        self.assertEqual(runtime.models["hr"]["services"]["configurator"]["image"], self.image)
        self.assertTrue(
            all(
                (self.root / f"data/release-{s}.json").stat().st_mode & 0o777 == 0o600
                for s in self.models
            )
        )
        self.models["hr"]["name"] = "foreign"
        with self.assertRaises(ValueError):
            self.runtime()
        self.assertFalse(any("up" in c or "stop" in c for c in self.calls))

    def test_migration_mark_precedes_migrate_and_x4_starts_after_frappe_and_dify(self):
        runtime = self.runtime()

        def marked():
            self.calls.append(["migration-mark"])

        with patch.object(runtime, "check"):
            runtime.apply(marked)
        migration = next(i for i, c in enumerate(self.calls) if "migrate" in c)
        mark = self.calls.index(["migration-mark"])
        x4 = next(i for i, c in enumerate(self.calls) if "up" in c and "frappe-mcp" in c)
        dify = next(i for i, c in enumerate(self.calls) if "up" in c and "api" in c)
        self.assertLess(mark, migration)
        self.assertLess(migration, dify)
        self.assertLess(dify, x4)
        self.fault = "migrate"
        self.calls.clear()
        with (
            patch.object(runtime, "check") as check,
            self.assertRaisesRegex(CollectionError, "^Frappe迁移:OSError$"),
        ):
            runtime.apply(marked)
        check.assert_not_called()
        self.assertFalse(any("up" in c and "frappe-mcp" in c for c in self.calls))

    def test_database_image_change_refused_before_stop_and_http_body_not_logged(self):
        self.previous["-hr/db"] = "sha256:" + "d" * 64
        with self.assertRaises(ValueError):
            self.runtime()
        self.assertFalse(any("up" in c or "stop" in c for c in self.calls))
        self.previous["-hr/db"] = self.image
        runtime = self.runtime()
        with patch.object(rt, "Collector") as collector, redirect_stdout(io.StringIO()) as output:
            collector.return_value.containers = {
                (suffix, name): {"Id": "c" * 64}
                for stack, suffix, *_ in rt.MODELS
                for name in runtime.selected[stack]
            }
            runtime.check()
        self.assertNotIn("private-sentinel", output.getvalue())
        self.assertIn("发布:HTTP:backend:200", output.getvalue())
