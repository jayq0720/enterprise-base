"""本机模拟HTTP机器人：限频、恢复、失败重试及告警脱敏。"""

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import health_alerts as alerts


class AlertTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.path = Path(work.name) / "health.json"
        self.messages: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(inner):
                self.messages.append(
                    json.loads(inner.rfile.read(int(inner.headers["Content-Length"])))
                )
                inner.send_response(200)
                inner.end_headers()
                inner.wfile.write(b'{"errcode":0}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.hook = f"http://127.0.0.1:{server.server_port}/hook?key=test-only"
        self.now = 1_800_000_000

    def update(self, healthy, at=0, sender=alerts.send):
        return alerts.update(
            self.path,
            {"container.hr.backend": (healthy, int(healthy))},
            self.hook,
            self.now + at,
            sender,
        )

    def test_real_http_hourly_limit_one_recovery_and_no_sensitive_fields(self):
        for healthy, at in (
            (False, 0),
            (False, 60),
            (False, 3599),
            (False, 3600),
            (True, 3601),
            (True, 4000),
        ):
            self.assertTrue(self.update(healthy, at))
        self.assertEqual(len(self.messages), 3)
        text = json.dumps(self.messages, ensure_ascii=False)
        self.assertEqual(text.count("已恢复"), 1)
        self.assertNotIn("test-only", text)
        self.assertNotIn("employee", text)
        self.assertEqual(set(self.messages[0]), {"msgtype", "text"})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_timeout_does_not_spam_and_recovery_failure_retries_hourly(self):
        fail = Mock(side_effect=TimeoutError("private employee webhook key"))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertFalse(self.update(False, 0, fail))
            self.update(False, 1, fail)
            self.assertEqual(fail.call_count, 1)
            self.assertFalse(self.update(True, 2, fail))
            self.update(True, 300, fail)
            self.assertEqual(fail.call_count, 2)
        self.assertNotIn("private employee webhook key", output.getvalue())
        self.assertTrue(self.update(True, 3602))
        self.update(True, 5000)
        self.assertEqual(len(self.messages), 1)
        self.assertIn("已恢复", self.messages[0]["text"]["content"])

    def test_blank_webhook_only_logs_and_bad_results_are_rejected(self):
        sender = Mock()
        with redirect_stdout(io.StringIO()) as output:
            self.assertTrue(alerts.update(self.path, {"memory": (False, 5)}, "", self.now, sender))
        sender.assert_not_called()
        self.assertIn("memory", output.getvalue())
        for result in (
            {"employee_name": (False, 1)},
            {"memory": (False, "private employee")},
            {"memory": (False, float("nan"))},
        ):
            with self.assertRaises(ValueError):
                alerts.update(self.path, result, self.hook, self.now)
        self.path.write_text('{"schema":1,"issues":{"memory":{"employee":"private"}}}')
        with self.assertRaises(ValueError):
            self.update(False)
        self.assertFalse(self.messages)
