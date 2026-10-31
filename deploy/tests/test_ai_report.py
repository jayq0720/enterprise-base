"""AI 助手周报：只读查询、汇总渲染；不接真实 Docker。"""

import json
import subprocess
import unittest
from unittest.mock import patch

import ai_report

DATA = {
    "runs": [
        {"app": "HR 助手", "runs": 10, "failed": 1, "avg_s": 2.1, "p95_s": 5.0, "tokens": 900}
    ],
    "people": [{"app": "HR 助手", "messages": 10, "users": 4, "conversations": 6}],
    "price": [{"app": "HR 助手", "currency": "RMB", "price": "0.0312"}],
    "fallback": [{"app": "HR 助手", "backup": 2, "rules": 1, "limited": 0, "model_errors": 3}],
    "intents": [{"app": "HR 助手", "intent": "balance", "n": 6}],
    "feedback": [{"app": "HR 助手", "rating": "dislike", "n": 1}],
    "disliked": [{"app": "HR 助手", "id": "msg-1"}],
    "failed": [],
}


class ReportTests(unittest.TestCase):
    def test_render_aggregates_only(self):
        text = ai_report.render(DATA, 7)
        self.assertIn("| HR 助手 | 10 | 4 | 6 | 2.1 | 5.0 | 900 | 0.03 RMB | 1（10%） | 2（20%） | 1 | 0 | 0/1 |", text)
        self.assertIn("balance 6", text)
        self.assertIn("`msg-1`", text)
        self.assertNotIn("出错的运行", text)

    def test_query_is_read_only_and_parses_single_line(self):
        out = subprocess.CompletedProcess([], 0, "BEGIN\n" + json.dumps({"runs": None, "failed": []}) + "\nROLLBACK\n", "")
        with patch.object(ai_report.subprocess, "run", return_value=out) as run:
            data = ai_report.query(3, "dify-db")
        self.assertEqual(data, {"runs": [], "failed": []})
        sql = run.call_args.kwargs["input"]
        self.assertTrue(sql.lstrip().startswith("begin transaction read only;"))
        self.assertIn("interval '3 days'", sql)
        self.assertEqual(run.call_args.args[0][:4], ["docker", "exec", "-i", "dify-db"])

    def test_query_failure(self):
        out = subprocess.CompletedProcess([], 2, "", "psql: error")
        with patch.object(ai_report.subprocess, "run", return_value=out), self.assertRaises(SystemExit):
            ai_report.query(7, None)

    def test_days_bounds(self):
        with patch("sys.argv", ["ai_report.py", "0"]), self.assertRaises(SystemExit):
            ai_report.main()


if __name__ == "__main__":
    unittest.main()
