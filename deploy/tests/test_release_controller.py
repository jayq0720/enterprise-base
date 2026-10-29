"""真实Git/状态/维护锁，Docker使用替身；验证故意失败及数据库回退调用。"""

import io
import unittest
from contextlib import nullcontext, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import release as core
import test_release_state as fixtures


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f = fixtures.StateTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.enterContext(patch.object(core, "ROOT", f.deploy))
        self.enterContext(patch.object(core.backup, "ROOT", f.deploy))
        self.enterContext(patch.object(core, "verify"))
        self.enterContext(
            patch.object(core, "git", side_effect=lambda *a: b"" if a[0] == "fetch" else f.git(*a))
        )

        def capture(env, *, locked):
            self.assertTrue(locked)
            with self.assertRaises(BlockingIOError), core.backup.operation():
                self.fail("备份入口前释放了发布锁")
            return f.archive

        self.enterContext(patch.object(core.backup, "backup", side_effect=capture))
        self.enterContext(patch.object(core.execution, "prepare"))
        self.old_image = "sha256:" + "d" * 64
        previous = {
            "-hr/backend": self.old_image,
            **{"-hr/" + n: self.old_image for n in core.state.IMAGE_KEYS.values()},
        }
        collector = Mock(images={k: dict(image_id=v) for k, v in previous.items()})
        self.enterContext(
            patch.object(
                core.execution, "running", return_value=(collector, set(previous), previous)
            )
        )
        self.runtime = Mock()
        self.runtime.stop.return_value = []
        self.runtime.apply.side_effect = self.apply
        self.enterContext(patch.object(core.execution, "Runtime", return_value=self.runtime))
        self.calls, self.fault = [], ""
        self.enterContext(patch.object(core, "command", side_effect=self.command))

    def command(self, args):
        self.calls.append(args)
        if self.fault == "build" and "build" in args:
            raise OSError("synthetic-private-content")
        if "--format" in args:
            return (
                self.fixture.before
                if "org.opencontainers.image.revision" in args[-2]
                else self.old_image
            ).encode()
        return b""

    def apply(self, migrate):
        if migrate:
            migrate()
        if self.fault == "migrate":
            raise OSError("synthetic-private-content")

    def invoke(self):
        with redirect_stdout(io.StringIO()) as output:
            record = core.release(self.fixture.target)
        self.assertNotIn("synthetic-private-content", output.getvalue())
        return record

    def test_image_preparation_failure_does_not_stop_environment_or_require_database(self):
        self.fault = "build"
        with redirect_stdout(io.StringIO()), self.assertRaises(OSError):
            self.invoke()
        self.runtime.apply.assert_not_called()
        self.runtime.stop.assert_not_called()
        record = next((self.fixture.deploy / "data/releases").glob("*/release.json"))
        self.assertFalse(core.state.load(record)["restore_database"])
        self.assertEqual(core.state.load(record)["phase"], "failed")
        self.assertFalse(any("stop" in c for c in self.calls))

    def test_migration_failure_records_database_requirement_and_rolls_back_data_before_start(self):
        self.fault = "migrate"
        with redirect_stdout(io.StringIO()), self.assertRaises(OSError):
            self.invoke()
        record = next((self.fixture.deploy / "data/releases").glob("*/release.json"))
        self.assertTrue(core.state.load(record)["restore_database"])
        with redirect_stdout(io.StringIO()), self.assertRaises(core.DatabaseRestoreRequired):
            core.rollback(record)
        self.fault = ""
        order = []
        with (
            patch.object(core.data, "prepared", return_value=nullcontext("payload")),
            patch.object(core.data, "confirm", return_value=core.data.Approval("synthetic")),
            patch.object(core.data, "inventory", return_value=[]),
            patch.object(core.data, "restore", side_effect=lambda *a: order.append("restore")),
            patch.object(core.sys, "stdin", SimpleNamespace(isatty=lambda: True)),
            redirect_stdout(io.StringIO()),
        ):
            self.runtime.apply.side_effect = lambda callback: order.append("start")
            core.rollback(record, "offline-key", True)
        self.assertEqual(order, ["restore", "start"])
        self.assertEqual(core.state.load(record)["phase"], "rolled-back")
        self.assertEqual(
            self.fixture.git("rev-parse", "HEAD").decode().strip(), self.fixture.before
        )
        self.assertEqual(
            core.values(self.fixture.env)["EB_KB_GATEWAY_IMAGE"],
            self.fixture.pins["EB_KB_GATEWAY_IMAGE"],
        )

    def test_success_and_config_change_refusal_without_touching_services(self):
        record = self.invoke()
        self.assertEqual(core.state.load(record)["phase"], "ready")
        self.fixture.env.write_text(self.fixture.env.read_text() + "EXTRA=changed\n")
        self.runtime.stop.reset_mock()
        with redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            core.rollback(record, "offline", True)
        self.runtime.stop.assert_not_called()

    def test_failed_data_restore_keeps_record_and_same_backup_can_retry(self):
        record = self.invoke()
        self.runtime.apply.reset_mock()
        restore = Mock(side_effect=[OSError("synthetic private failure"), None])
        with (
            patch.object(core.data, "prepared", return_value=nullcontext("same-backup")) as prepare,
            patch.object(
                core.data, "confirm", return_value=core.data.Approval("synthetic")
            ) as confirm,
            patch.object(core.data, "inventory", return_value=[]),
            patch.object(core.data, "restore", restore),
            patch.object(core.sys, "stdin", SimpleNamespace(isatty=lambda: True)),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(OSError):
                core.rollback(record, "offline-key", True)
            self.assertEqual(core.state.load(record)["phase"], "rollback-failed")
            self.assertTrue(core.state.load(record)["restore_database"])
            self.runtime.apply.assert_not_called()
            self.runtime.stop.assert_called_once()
            core.rollback(record, "offline-key", True)
        self.assertEqual(core.state.load(record)["phase"], "rolled-back")
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(confirm.call_count, 2)
        self.assertEqual(prepare.call_args_list[0], prepare.call_args_list[1])
        self.assertEqual(restore.call_count, 2)
        self.runtime.apply.assert_called_once_with(None)
