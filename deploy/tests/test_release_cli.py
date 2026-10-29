"""实际Make接线与CLI脱敏；不执行生产发布、也不打开离线私钥。"""

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import release_cli as cli


class CliTests(unittest.TestCase):
    def test_make_missing_revision_fails_and_overwrite_flag_requires_explicit_option(self):
        deploy = str(Path(cli.__file__).parent)
        result = subprocess.run(
            ["make", "--silent", "-C", deploy, "release", "PYTHON=" + sys.executable, "REVISION="],
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("必须指定REVISION", result.stderr.decode())
        for selected in ("0", "1"):
            preview = subprocess.check_output(
                ["make", "--silent", "-C", deploy, "-n", "rollback", "RESTORE_DATA=" + selected],
                text=True,
            )
            self.assertEqual("--restore-data" in preview, selected == "1")

    def test_native_error_is_bounded_without_private_content(self):
        output = io.StringIO()
        with (
            patch.object(cli.sys, "argv", ["release_cli.py", "release", "--revision", "a" * 40]),
            patch.object(cli, "release", side_effect=OSError("private employee secret")),
            redirect_stderr(output),
        ):
            self.assertEqual(cli.main(), 1)
        self.assertNotIn("private employee secret", output.getvalue())
        self.assertIn("release:失败:OSError", output.getvalue())

    def test_database_and_terminal_requirements_are_actionable_and_never_bypassed(self):
        for exception, text in (
            (cli.DatabaseRestoreRequired, "RESTORE_DATA=1"),
            (cli.InteractiveTerminalRequired, "交互终端"),
        ):
            output = io.StringIO()
            with (
                patch.object(cli.sys, "argv", ["release_cli.py", "rollback"]),
                patch.object(cli, "rollback", side_effect=exception),
                redirect_stderr(output),
            ):
                self.assertEqual(cli.main(), 1)
            self.assertIn(text, output.getvalue())
