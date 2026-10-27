"""真实 Git 工作目录及 Compose 解析；镜像查询、prepare 和资源清单为协议替身。"""

import json
import os
import shlex
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import restore_workspace as ws


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name).resolve()
        self.repo = self.root / "repo"
        (self.repo / "deploy").mkdir(parents=True)
        (self.repo / ".gitignore").write_text("deploy/secrets/\ndeploy/data/\n")
        (self.repo / "deploy/prepare.py").write_text(
            "# 临时 Git/Compose 测试目录，不连接业务服务。\n"
        )
        self.git("init")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        self.git("add", ".")
        self.git(
            "-c", "user.name=CI", "-c", "user.email=ci@test.invalid", "commit", "-m", "合成测试"
        )
        revision = self.git("rev-parse", "HEAD").decode().strip()
        self.target = self.repo / "deploy/data/restores/recovery"
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)
        ws.write(
            self.source / ".env",
            "EB_PROJECT=source\nEB_DOMAIN=original.test\nSECRET_KEY=synthetic-key\nDB_PASSWORD=old-db\nEB_FRAPPE_DB_PASSWORD=old-root\nEB_FRAPPE_ADMIN_PASSWORD=old-admin\n",
        )
        ws.write(self.source / "binary.pfx", b"\xff\x00")
        self.image = "sha256:" + "a" * 64
        self.manifest = dict(
            project="source",
            revision=revision,
            images={"/caddy": dict(image_id=self.image), "-dify/api": dict(image_id=self.image)},
        )
        self.failure, self.envs = "", []

    def git(self, *args):
        return ws.command(["git", "-C", str(self.repo), *args])

    def fake_prepare(self):
        deploy = self.target / "deploy"
        (deploy / "data").mkdir(mode=0o700)
        config = ws.values(deploy / "secrets/.env")
        ws.write(deploy / "secrets/dify.env", "DB_PASSWORD=" + config["DB_PASSWORD"] + "\n")
        for suffix, path, services in (
            ("", "docker-compose.yml", {"caddy": "/data"}),
            (
                "-dify",
                "data/dify/docker/eb-compose.yaml",
                {"api": "/app/api/storage", "worker": "/app/api/storage"},
            ),
            ("-hr", "data/frappe/eb-compose.yaml", {"frontend": "/home/frappe/frappe-bench/sites"}),
        ):
            model: dict[str, Any] = dict(
                name="recovery" + suffix, services={}, volumes=dict(store={})
            )
            for service, mount in services.items():
                model["services"][service] = dict(
                    image="official:1.2.3",
                    environment=dict(
                        DB_PASSWORD="${DB_PASSWORD}",
                        CONSOLE_API_URL="http://dify.recovery.localhost",
                    ),
                    volumes=[dict(type="volume", source="store", target=mount)],
                )
            target = deploy / path
            target.parent.mkdir(parents=True, exist_ok=True)
            ws.write(target, json.dumps(model))

    def execute(self, args, source=None, env=None):
        if args[0] == "git":
            return ws.command(args)
        if env is not None:
            self.envs.append(env)
        if args[0] == sys.executable:
            self.fake_prepare()
            return b""
        if args[1:3] == ["image", "inspect"]:
            return self.image.encode()
        if args[1] == "compose":
            if self.failure:
                raise OSError("synthetic private content")
            cli = shlex.split(os.environ.get("EB_COMPOSE_COMMAND", "docker compose"))
            return ws.command([*cli, *args[2:]], env=env)
        return b""

    def create(self, port=None):
        if port is None:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        return ws.create(
            self.repo,
            self.manifest,
            self.source,
            ["hr.original.test"],
            "recovery",
            self.target,
            port,
            self.execute,
        )

    def test_actual_git_compose_isolation_credentials_binary_copy_and_all_writers_nocopy(self):
        with patch.dict(os.environ, DB_PASSWORD="host-password", COMPOSE_PROJECT_NAME="source"):
            deploy, primary = self.create()
        result = ws.values(deploy / "secrets/.env")
        self.assertEqual(
            (result["EB_PROJECT"], result["EB_BIND"], result["SECRET_KEY"]),
            ("recovery", "127.0.0.1", "synthetic-key"),
        )
        self.assertNotIn(result["DB_PASSWORD"], {"old-db", "host-password"})
        self.assertNotEqual(result["EB_FRAPPE_DB_PASSWORD"], "old-root")
        self.assertEqual((deploy / "secrets/binary.pfx").read_bytes(), b"\xff\x00")
        self.assertTrue(
            all("DB_PASSWORD" not in env and "COMPOSE_PROJECT_NAME" not in env for env in self.envs)
        )
        edge = json.loads((deploy / "data/restore-edge.json").read_text())
        dify = json.loads((deploy / "data/restore-dify.json").read_text())
        hr = json.loads((deploy / "data/restore-hr.json").read_text())
        self.assertEqual(edge["services"]["caddy"]["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(edge["services"]["caddy"]["image"], self.image)
        self.assertTrue(all(s["volumes"][0]["volume"]["nocopy"] for s in dify["services"].values()))
        self.assertEqual(
            hr["services"]["frontend"]["environment"]["FRAPPE_SITE_NAME_HEADER"], primary
        )
        self.assertFalse(hr["services"]["frontend"]["volumes"][0].get("volume", {}).get("nocopy"))
        self.assertTrue(
            all(p.stat().st_mode & 0o777 == 0o600 for p in (deploy / "data").glob("restore-*.json"))
        )

    def test_failure_after_secret_copy_cleans_directory_and_git_registration(self):
        self.failure = "compose"
        with self.assertRaises(ws.CollectionError) as error:
            self.create()
        self.assertEqual(str(error.exception), "恢复目录:OSError")
        self.assertFalse(self.target.exists())
        self.assertNotIn(str(self.target).encode(), self.git("worktree", "list", "--porcelain"))

    def test_existing_directory_occupied_port_and_unsafe_secret_refuse_creation(self):
        self.target.mkdir(parents=True)
        marker = self.target / "untouched"
        marker.write_text("keep")
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(marker.read_text(), "keep")
        marker.unlink()
        self.target.rmdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            with self.assertRaises(OSError):
                self.create(probe.getsockname()[1])
        (self.source / ".env").chmod(0o644)
        with self.assertRaises(ValueError):
            self.create()
        self.assertFalse(self.target.exists())
