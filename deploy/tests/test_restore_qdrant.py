"""验证恢复隔离和故障清理；真实快照/检索由独立原生 CI 演练。"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import restore_qdrant as qd


class QdrantTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.snapshot = Path(self.work.name) / "qdrant.snapshot"
        self.snapshot.write_bytes(b"synthetic")
        self.snapshot.chmod(0o600)
        self.image, self.identity = "sha256:" + "a" * 64, "b" * 64
        self.manifest = dict(images={"-dify/qdrant": dict(image_id=self.image)})
        self.calls, self.failure, self.created, self.running = [], "", 0, True
        self.item: dict[str, Any] = dict(
            Id=self.identity,
            Image=self.image,
            State=dict(Running=False),
            Config=dict(Labels={qd.LABEL + "project": "new-dify", qd.LABEL + "service": "qdrant"}),
            Mounts=[dict(Destination="/qdrant/storage", Name="new_qdrant", Type="volume", RW=True)],
        )
        self.enterContext(patch.object(qd.pg, "target"))

    def docker(self, args, source=None):
        self.calls.append(args)
        if args[1] == self.failure:
            raise subprocess.CalledProcessError(1, args, stderr=b"private secret employee")
        if args[1] == "create":
            self.created += 1
            return str(self.created).encode()
        if args[1] == "volume":
            return json.dumps(
                [dict(Name="new_qdrant", Labels={qd.LABEL + "project": "new-dify"})]
            ).encode()
        if args[1] == "inspect":
            return json.dumps(
                [
                    self.item
                    if args[-1] == self.identity
                    else dict(State=dict(Running=self.running, ExitCode=0))
                ]
            ).encode()
        if args[1] == "stop":
            self.running = False
        return b""

    def restore(self):
        qd.restore(
            "new", self.manifest, "pg", self.identity, self.snapshot, self.docker, lambda _: None
        )

    def test_native_storage_snapshot_no_force_no_network_and_target_stays_stopped(self):
        self.restore()
        creates = [c for c in self.calls if c[1] == "create"]
        self.assertIn("--storage-snapshot", creates[1])
        self.assertIn("--disable-telemetry", creates[1])
        self.assertNotIn("--force-snapshot", creates[1])
        self.assertTrue(all(c[c.index("--network") + 1] == "none" for c in creates))
        self.assertEqual(sum(c[1] == "rm" for c in self.calls), 2)
        self.assertFalse(any(c[1] == "start" and c[-1] == self.identity for c in self.calls))

    def test_invalid_target_and_public_snapshot_refuse_recovery(self):
        for defect in ("running", "old-project", "image", "bind", "permissions"):
            original = json.loads(json.dumps(self.item))
            if defect == "running":
                self.item["State"]["Running"] = True
            if defect == "old-project":
                self.item["Config"]["Labels"][qd.LABEL + "project"] = "source-dify"
            if defect == "image":
                self.item["Image"] = "sha256:wrong"
            if defect == "bind":
                self.item["Mounts"][0]["Type"] = "bind"
            self.snapshot.chmod(0o644 if defect == "permissions" else 0o600)
            self.calls.clear()
            with self.assertRaises(qd.CollectionError):
                self.restore()
            self.assertFalse(any(c[1] == "create" for c in self.calls))
            self.item = original

    def test_native_failure_or_timeout_removes_recovery_container_and_sanitizes_error(self):
        for failure in ("cp", "exec", "stop"):
            self.failure, self.running, self.calls = failure, True, []
            with self.assertRaises(qd.CollectionError) as error:
                self.restore()
            self.assertNotIn("private", str(error.exception))
            self.assertEqual(sum(c[1] == "rm" for c in self.calls), 2)
            self.assertFalse(any(c[1] == "start" and c[-1] == self.identity for c in self.calls))
