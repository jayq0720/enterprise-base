"""真实 age、操作锁、信号及 HTTP；业务 Docker 仍使用替身。"""

import io
import json
import os
import signal
import subprocess
import sys
import threading
import unittest
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import backup_bundle as bundle
import restore as entry
import restore_runtime as rt
import test_restore_guard as fixtures


class EntryTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.GuardTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        folder, metadata = fixture.packet()
        self.identity, self.archive = self.root / "identity", self.root / "backup.age"
        keygen = bundle.tool("age-keygen")
        subprocess.run([keygen, "-o", str(self.identity)], check=True, stderr=subprocess.DEVNULL)
        recipient = subprocess.check_output([keygen, "-y", str(self.identity)], text=True).strip()
        bundle.pack(folder, self.archive, recipient, metadata)
        self.target = Mock()
        self.target.stop.side_effect = self.stopped
        original = entry.guard.new_project
        self.enterContext(
            patch.object(
                entry.guard,
                "new_project",
                side_effect=lambda project, source, directory, run: original(
                    project, source, directory, lambda args: b""
                ),
            )
        )
        self.enterContext(patch.object(entry, "ROOT", self.root))
        self.enterContext(patch.object(entry.guard.backup, "ROOT", self.root))
        self.enterContext(patch.object(entry.ws, "create", side_effect=self.create))
        self.factory = self.enterContext(patch.object(entry, "Target", return_value=self.target))
        self.old = self.root / "original"
        self.old.write_text("untouched")

    def create(self, repo, manifest, source, sites, project, destination, port):
        deploy = destination / "deploy"
        (deploy / "data").mkdir(parents=True)
        self.target.deploy = deploy
        return deploy, sites[0]

    def stopped(self):
        # 失败停服必须在同一操作锁释放之前发生。
        with self.assertRaises(BlockingIOError), entry.guard.backup.operation():
            self.fail("恢复失败时提前释放了锁")
        return []

    def test_success_native_failure_and_sigterm_clean_plaintext_and_keep_original(self):
        for state in ("ready", "failed", "cancelled"):
            with self.subTest(state=state):
                if state == "failed":
                    self.target.restore.side_effect = rt.CollectionError("Frappe:OSError")
                elif state == "cancelled":
                    self.target.restore.side_effect = lambda *a: os.kill(
                        os.getpid(), signal.SIGTERM
                    )
                else:
                    self.target.restore.side_effect = None
                self.target.stop.reset_mock()

                def invoke(state=state):
                    entry.restore(self.archive, self.identity, "recovery-" + state, 18080)

                if state == "ready":
                    invoke()
                    self.target.stop.assert_not_called()
                else:
                    with self.assertRaises(
                        rt.CollectionError if state == "failed" else InterruptedError
                    ):
                        invoke()
                    self.target.stop.assert_called_once()
                result = json.loads((self.target.deploy / "data/restore-result.json").read_text())
                self.assertEqual(result["state"], "ready" if state == "ready" else "failed")
                self.assertFalse(list((self.root / "data").glob(".restore-clear-*")))
                self.assertEqual(self.old.read_text(), "untouched")

    def test_corrupt_archive_does_not_construct_target_and_cli_redacts_errors(self):
        data = self.archive.read_bytes()
        self.archive.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        with self.assertRaises(subprocess.CalledProcessError):
            entry.restore(self.archive, self.identity, "recovery", 18080)
        self.factory.assert_not_called()
        output = io.StringIO()
        with (
            patch.object(entry, "restore", side_effect=ValueError("private employee secret")),
            patch.object(
                entry.sys, "argv", ["restore.py", "--backup", "unused", "--identity", "unused"]
            ),
            redirect_stderr(output),
        ):
            self.assertEqual(entry.main(), 1)
        self.assertEqual(output.getvalue(), "恢复:失败:ValueError\n")

    def test_real_http_ping_redirect_and_fail_closed(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            status = 200

            def do_GET(self):
                requests.append(self.headers["Host"])
                self.send_response(self.status if self.path.endswith("ping") else 307)
                self.end_headers()
                self.wfile.write(b'{"message":"pong"}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            rt.verify(server.server_port, "recovery.localhost")
            self.assertEqual(requests, ["hr.recovery.localhost", "dify.recovery.localhost"])
            Handler.status = 403
            with (
                patch.object(rt.time, "sleep"),
                self.assertRaisesRegex(rt.CollectionError, "^HTTP:Unhealthy$"),
            ):
                rt.verify(server.server_port, "recovery.localhost")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_make_target_rejects_missing_files_before_any_docker_work(self):
        result = subprocess.run(
            [
                "make",
                "--silent",
                "-C",
                str(Path(entry.__file__).parent),
                "restore",
                "PYTHON=" + sys.executable,
                "BACKUP=",
                "IDENTITY=",
            ],
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("必须指定 BACKUP 和 IDENTITY 文件", result.stderr.decode())
