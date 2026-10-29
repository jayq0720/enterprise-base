"""阈值边界、备份UTC时间、只读磁盘探针和失败脱敏。"""

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import health_metrics as metrics


class MetricTests(unittest.TestCase):
    def test_memory_limits_and_backup_time_not_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            memory = root / "meminfo"
            memory.write_text("MemTotal: 1000 kB\nMemAvailable: 100 kB\n")
            self.assertEqual(metrics.memory(memory), 10)
            memory.write_text("MemTotal: 0 kB\nMemAvailable: 0 kB\n")
            with self.assertRaises(ValueError):
                metrics.memory(memory)
            archive = root / "eb-20261028T000000Z-12345678.tar.age"
            archive.write_bytes(b"encrypted-test")
            archive.chmod(0o600)
            now = datetime(2026, 10, 29, tzinfo=UTC).timestamp()
            cfg = dict(EB_PROJECT="eb", EB_BACKUP_DIR=str(root))
            self.assertEqual(metrics.backup_age(root, cfg, now), 24)
            archive.unlink()
            with self.assertRaises(ValueError):
                metrics.backup_age(root, cfg, now)
        for value in ("nan", "-1", "101"):
            with self.assertRaises(ValueError):
                metrics.limits({"EB_HEALTH_DISK_MIN_PERCENT": value})

    def test_disk_uses_all_owned_volumes_read_only_and_always_cleans(self):
        containers = {
            ("hr", "backend"): dict(
                Image="sha256:" + "a" * 64, Mounts=[dict(Type="volume", Name="eb-hr_sites")]
            )
        }
        record: dict = dict(Name="eb-hr_sites", Labels={"com.docker.compose.project": "eb-hr"})
        run = Mock(side_effect=[json.dumps([record]).encode(), b"created", b"15", b""])
        self.assertEqual(metrics.disk(containers, "eb", run), 15)
        creation = run.call_args_list[1].args[0]
        self.assertIn(
            "type=volume,source=eb-hr_sites,target=/volumes/0,readonly,volume-nocopy", creation
        )
        self.assertIn("none", creation)
        self.assertEqual(run.call_args.args[0][:3], ["docker", "rm", "-f"])
        run.side_effect = [json.dumps([record]).encode(), b"created", TimeoutError("private"), b""]
        with self.assertRaises(TimeoutError):
            metrics.disk(containers, "eb", run)
        self.assertEqual(run.call_args.args[0][:3], ["docker", "rm", "-f"])
        record["Labels"]["com.docker.compose.project"] = "foreign"
        run.reset_mock(side_effect=True)
        run.return_value = json.dumps([record]).encode()
        with self.assertRaises(ValueError):
            metrics.disk(containers, "eb", run)
        self.assertEqual(run.call_count, 1)

    def test_thresholds_tls_and_failure_only_return_numeric_values(self):
        cfg = dict(EB_SCHEME="https", EB_PROJECT="eb")
        with (
            patch.object(metrics, "memory", return_value=10),
            patch.object(metrics, "disk", return_value=15),
            patch.object(metrics, "backup_age", return_value=26),
            patch.object(metrics, "certificate", return_value=14),
        ):
            result = metrics.check(cfg, Path("."), {}, Mock(), 100)
            self.assertTrue(all(ok for ok, _ in result.values()))
            self.assertIn("tls.wecom", result)
        with (
            patch.object(metrics, "memory", side_effect=ValueError("employee private")),
            patch.object(metrics, "disk", return_value=14),
            patch.object(metrics, "backup_age", return_value=27),
        ):
            result = metrics.check({**cfg, "EB_SCHEME": "http"}, Path("."), {}, Mock(), 100)
            self.assertEqual(result["memory"], (False, None))
            self.assertTrue(all(not ok for ok, _ in result.values()))
            self.assertNotIn("employee", json.dumps(result))
