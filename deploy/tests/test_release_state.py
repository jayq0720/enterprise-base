"""真实 Git main 边界、加密包留存和原子迁移标记；不启动容器。"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import release_state as state


class StateTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.repo = Path(work.name).resolve()
        self.deploy = self.repo / "deploy"
        self.deploy.mkdir()
        (self.deploy / "secrets").mkdir(mode=0o700)
        (self.repo / ".gitignore").write_text("deploy/secrets/\ndeploy/data/\n")
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Synthetic")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.pins = {
            key: "ghcr.io/example/enterprise-base-" + name + "@sha256:" + "a" * 64
            for key, name in state.IMAGE_KEYS.items()
        }
        self.lock = dict(
            frappe_image="enterprise-base/frappe:15.123.1-eb0.1.0",
            sources=dict(dify=dict(commit="a" * 40)),
        )
        (self.deploy / "upstream.lock.json").write_text(json.dumps(self.lock))
        self.example = self.deploy / ".env.example"
        self.example.write_text("".join(f"{k}={v}\n" for k, v in self.pins.items()))
        self.git("add", ".")
        self.git("commit", "-m", "合成旧版本")
        self.before = self.git("rev-parse", "HEAD").decode().strip()
        self.env = self.deploy / "secrets/.env"
        self.env.write_text(
            self.example.read_text()
            + "EB_PROJECT=synthetic\nSECRET_KEY=synthetic-private-content\n"
        )
        self.env.chmod(0o600)
        self.example.write_text(self.example.read_text().replace("a" * 64, "b" * 64))
        self.git("add", ".")
        self.git("commit", "-m", "合成新版本")
        self.target = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-ref", "refs/remotes/origin/main", self.target)
        self.git("checkout", "--detach", self.before)
        self.archive = self.repo / "synthetic.tar.age"
        self.archive.write_bytes(b"age-encryption.org/v1\nsynthetic encrypted bytes")
        self.archive.chmod(0o600)
        # 测试输入放仓库外，避免给真实 Git 干净检查制造未跟踪文件。
        self.archive = self.archive.rename(self.repo.parent / (self.repo.name + ".age"))
        self.addCleanup(self.archive.unlink, missing_ok=True)

    def git(self, *args):
        return state.command(["git", "-C", str(self.repo), *args])

    def plan(self):
        return state.plan(self.repo, self.target, self.env)

    def test_main_pins_record_no_secrets_and_backup_survives_retention(self):
        plan = self.plan()
        self.assertEqual(plan["before"]["revision"], self.before)
        self.assertTrue(all("b" * 64 in v for v in plan["target"]["images"].values()))
        path = state.create(
            self.deploy,
            plan,
            self.archive,
            {"-hr/backend": dict(image_id="sha256:" + "c" * 64, Env=["private employee"])},
        )
        self.archive.unlink()
        record = state.load(path)
        text = path.read_text()
        for forbidden in ("SECRET_KEY", "synthetic-private-content", "private employee", "Env"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        state.mark(path, "migration-started")
        self.assertTrue(state.mark(path, "failed")["restore_database"])
        self.assertTrue(state.mark(path, "rollback-failed")["restore_database"])
        self.assertEqual(record["backup"], "before.tar.age")
        self.assertEqual(record["running_services"], ["-hr/backend"])
        (path.parent / record["backup"]).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            state.load(path)

    def test_release_from_version_without_portal(self):
        # 旧版本没有前台：配置里多出的前台镜像（例如回退后留下的）不影响比对，目标版本带上前台
        self.git("checkout", "--detach", self.target)
        old = "".join(f"{k}={v}\n" for k, v in self.pins.items() if k != "EB_PORTAL_IMAGE")
        self.example.write_text(old)
        self.git("commit", "-am", "合成无前台版本")
        before = self.git("rev-parse", "HEAD").decode().strip()
        portal = self.pins["EB_PORTAL_IMAGE"].replace("a" * 64, "b" * 64)
        self.example.write_text(old.replace("a" * 64, "b" * 64) + f"EB_PORTAL_IMAGE={portal}\n")
        self.git("commit", "-am", "合成加前台版本")
        target = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-ref", "refs/remotes/origin/main", target)
        self.git("checkout", "--detach", before)
        plan = state.plan(self.repo, target, self.env)
        self.assertIn("EB_PORTAL_IMAGE", plan["target"]["images"])
        self.env.write_text(self.env.read_text().replace("EB_POLICY_SYNC_IMAGE=", "EB_POLICY_SYNC_IMAGE=x"))
        with self.assertRaises(ValueError):
            state.plan(self.repo, target, self.env)

    def test_dirty_worktree_pr_commit_wrong_service_floating_pin_and_permissions_refused(self):
        stray = self.repo / "untracked"
        stray.touch()
        with self.assertRaises(ValueError):
            self.plan()
        stray.unlink()
        self.env.chmod(0o644)
        with self.assertRaises(ValueError):
            self.plan()
        self.env.chmod(0o600)
        for value in ("ghcr.io/example/service:latest", self.pins["EB_POLICY_SYNC_IMAGE"]):
            bad = dict(self.pins, EB_KB_GATEWAY_IMAGE=value)
            with self.assertRaises(ValueError):
                state.images(bad)
        # 员工前台可选；其它服务缺项、未知镜像项或前台镜像对不上都拒绝
        core = {k: v for k, v in self.pins.items() if k != "EB_PORTAL_IMAGE"}
        self.assertEqual(state.images(core), core)
        for bad in (
            {k: v for k, v in core.items() if k != "EB_KB_GATEWAY_IMAGE"},
            dict(self.pins, EB_OTHER_IMAGE=self.pins["EB_PORTAL_IMAGE"]),
            dict(self.pins, EB_PORTAL_IMAGE=self.pins["EB_FRAPPE_MCP_IMAGE"]),
        ):
            with self.assertRaises(ValueError):
                state.images(bad)
        self.git("checkout", "--orphan", "preview")
        (self.repo / "preview").touch()
        self.git("add", ".")
        self.git("commit", "-m", "合成 PR 提交")
        preview = self.git("rev-parse", "HEAD").decode().strip()
        self.git("checkout", "--detach", self.before)
        with self.assertRaises(subprocess.CalledProcessError):
            state.plan(self.repo, preview, self.env)
        self.git("checkout", "--detach", self.target)
        with self.assertRaises(subprocess.CalledProcessError):
            state.plan(self.repo, self.before, self.env)

    def test_failed_atomic_write_keeps_previous_record(self):
        path = state.create(self.deploy, self.plan(), self.archive, {})
        previous = path.read_bytes()
        with patch.object(state.os, "replace", side_effect=OSError("private output")):
            with self.assertRaises(OSError):
                state.mark(path, "migration-started")
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(
            {p.name for p in path.parent.iterdir()}, {"release.json", "before.tar.age"}
        )
        bad = self.plan()
        bad["before"]["password"] = "private"
        with self.assertRaises(ValueError):
            state.create(self.deploy, bad, self.archive, {})
