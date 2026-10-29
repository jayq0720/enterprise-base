"""真实解密与确认门槛；覆盖写入的Docker命令使用替身。"""

import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import backup_bundle as bundle
import rollback_data as data
import test_restore_guard as fixtures
import test_restore_runtime as transport
import test_restore_storage as archives


class DataTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.GuardTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        (self.root / "data").mkdir(mode=0o700)
        self.enterContext(patch.object(data, "ROOT", self.root))
        self.identity = self.root / "identity"
        keygen = bundle.tool("age-keygen")
        subprocess.run([keygen, "-o", str(self.identity)], check=True, stderr=subprocess.DEVNULL)
        recipient = subprocess.check_output([keygen, "-y", str(self.identity)], text=True).strip()
        packet, self.manifest = fixture.packet()
        self.image = "sha256:" + "a" * 64
        names = [
            "/caddy",
            "/wechat",
            "-hr/backend",
            "-dify/api",
            "-dify/plugin_daemon",
            "-dify/qdrant",
        ]
        self.manifest["images"] = {n: dict(image_id=self.image) for n in names}
        for name in ("api", "plugin_daemon", "caddy", "wechat"):
            archives.StorageTests().packet(packet / "payload" / (name + ".tar"))
        self.archive = self.root / "snapshot.age"
        bundle.pack(packet, self.archive, recipient, self.manifest)
        self.query = Mock(return_value=self.image.encode())

    def prepared(self, project="source"):
        return data.prepared(self.archive, self.identity, project, "a" * 40, self.query)

    def test_real_age_refuses_mismatch_and_cleans_all_plaintext(self):
        with self.assertRaises(ValueError), self.prepared("other"):
            self.fail("错误来源不得进入覆盖阶段")
        self.query.assert_not_called()
        with self.prepared() as payload:
            self.assertEqual(payload[0]["project"], "source")
            self.assertTrue((payload[1] / "sites/0/site_config.json").is_file())
        self.assertFalse(list((self.root / "data").glob(".rollback-clear-*")))

    def test_confirmation_requires_terminal_and_both_correct_answers(self):
        for terminal, answers in ((False, []), (True, ["wrong"]), (True, ["source", "wrong"])):
            with self.subTest(terminal=terminal, answers=answers), redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    data.confirm("source", Mock(side_effect=answers), terminal)
        with redirect_stdout(io.StringIO()):
            approval = data.confirm(
                "source", Mock(side_effect=["source", "确认丢失发布后数据"]), True
            )
        self.assertEqual(approval, data.Approval("source"))

    def test_live_writer_refused_before_creating_or_overwriting_anything(self):
        runtime = Mock(project="source", prefix="release")
        record = dict(
            Id="c" * 64,
            State=dict(Running=True),
            Config=dict(
                Labels={
                    "com.docker.compose.project": "source",
                    "com.docker.compose.service": "caddy",
                }
            ),
        )
        runtime.call.side_effect = lambda args: (
            b"id" if args[1] == "ps" else json.dumps([record]).encode()
        )
        with self.assertRaises(ValueError):
            data.restore(runtime, (self.manifest, None, None, None), data.Approval("source"))
        runtime.compose.assert_not_called()
        with self.assertRaises(ValueError):
            data.restore(runtime, (self.manifest, None, None, None), data.Approval("other"))

    def test_volume_preflight_precedes_database_and_frappe_restore_uses_existing_transport(self):
        with self.prepared() as payload:
            (self.root / "secrets").mkdir()
            (self.root / "secrets/.env").write_text("REDIS_PASSWORD=synthetic\n")
            runtime = Mock(project="source", prefix="release", deploy=self.root, env={})
            runtime.models = {
                stack: dict(services={name: {} for name in names})
                for stack, names in (
                    ("edge", ["caddy", "wechat"]),
                    ("hr", ["backend"]),
                    ("dify", ["api", "plugin_daemon", "qdrant"]),
                )
            }
            runtime.call.return_value = b""
            runtime.container.return_value = "c" * 64
            order = []

            def destination(project, manifest, suffix, name, path, run):
                order.append("preflight:" + name)
                return "source" + suffix + "_" + name

            with (
                patch.object(data.pg, "target"),
                patch.object(data.storage, "destination", side_effect=destination),
                patch.object(
                    data.existing_pg, "restore", side_effect=lambda *a: order.append("database")
                ) as restore_pg,
                patch.object(data.storage, "helper") as write_volume,
                patch.object(data.qd, "recover"),
            ):
                restore_pg.side_effect = OSError("synthetic restore failure")
                with self.assertRaises(OSError):
                    data.restore(runtime, payload, data.Approval("source"))
                runtime.frappe.assert_not_called()
                write_volume.assert_not_called()
                # PostgreSQL中途失败后保留同一解密备份；重试仍先预检，再完整覆盖。
                restore_pg.side_effect = lambda *a: order.append("database")
                data.restore(runtime, payload, data.Approval("source"))
                self.assertEqual(restore_pg.call_count, 2)
            self.assertEqual(order[-1], "database")
            self.assertEqual(len(order), 11)
            runtime.frappe.assert_called_once_with(
                payload[1], payload[2], payload[2][0], 0, existing=True
            )
        fixture = transport.RuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.target.frappe(fixture.root, ["hr.source"], "hr.source", 0, existing=True)
        commands = [args for args, _, _ in fixture.calls]
        self.assertFalse(any("host_name" in command for command in commands))
        calls = [
            (args, env) for args, _, env in fixture.calls if "EB_RESTORE_EXISTING_APPROVED" in args
        ]
        self.assertTrue(calls)
        self.assertEqual(calls[0][1]["EB_RESTORE_EXISTING_APPROVED"], "hr.source")
