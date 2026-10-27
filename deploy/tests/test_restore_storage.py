"""真实 tar 校验与 Docker 协议；实际空卷写入另由原生 CI 演练。"""

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import restore_storage as storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.image = "sha256:" + "a" * 64
        self.manifest: dict[str, Any] = dict(
            project="source", images={"-hr/backend": dict(image_id=self.image)}
        )
        self.items: dict[str, Any] = {}
        self.calls, self.created, self.bad_volume = [], [], ""
        self.guard = self.enterContext(patch.object(storage.pg, "target"))
        for suffix, service, destination in storage.VOLUMES:
            self.manifest["images"][suffix + "/" + service] = dict(image_id=self.image)
            self.items[service] = dict(
                Config=dict(
                    Labels={
                        storage.LABEL + "project": "recovery" + suffix,
                        storage.LABEL + "service": service,
                    }
                ),
                State=dict(Running=False),
                Image=self.image,
                Mounts=[dict(Destination=destination, Type="volume", RW=True, Name=service)],
            )
            self.packet(self.root / (service + ".tar"))

    def packet(self, path, defect=""):
        with tarfile.open(path, "w") as output:
            item = tarfile.TarInfo("../escape" if defect == "traversal" else "payload")
            item.size = 1
            if defect == "device":
                item.type, item.size = tarfile.CHRTYPE, 0
            output.addfile(item, io.BytesIO(b"x"))
            if defect == "link-child":
                link = tarfile.TarInfo("link")
                link.type, link.linkname = tarfile.SYMTYPE, "/outside"
                output.addfile(link)
                output.addfile(tarfile.TarInfo("link/child"))
        path.chmod(0o600)

    def docker(self, args, source=None):
        self.calls.append((args, source))
        if args[1] == "ps":
            return args[-1].split("=")[-1].encode()
        if args[1] == "inspect":
            return json.dumps([self.items[args[-1]]]).encode()
        if args[1] == "volume":
            suffix = "-dify" if args[-1] in {"api", "plugin_daemon"} else ""
            return json.dumps(
                [dict(Name=args[-1], Labels={storage.LABEL + "project": "recovery" + suffix})]
            ).encode()
        if args[1] == "create":
            self.created.append(args)
            return str(len(self.created)).encode()
        if args[1] == "start" and self.bad_volume:
            if (
                "src=" + self.bad_volume + ","
                in self.created[int(args[-1]) - 1][
                    self.created[int(args[-1]) - 1].index("--mount") + 1
                ]
            ):
                raise PermissionError("private secret employee")
        return b""

    def test_all_four_volumes_preflight_then_stream_and_cleanup_without_activation(self):
        storage.restore("recovery", self.manifest, "pg", self.root, self.docker)
        self.guard.assert_called_once_with("recovery", self.manifest, "pg", self.docker)
        self.assertEqual(
            [p.name for _, p in self.calls if p], [s + ".tar" for _, s, _ in storage.VOLUMES]
        )
        for args in self.created:
            self.assertIn("--read-only", args)
            self.assertIn("no-new-privileges", args)
            self.assertNotIn("--env-file", args)
            self.assertNotIn("--volumes-from", args)
        self.assertEqual(sum(c[1] == "rm" for c, _ in self.calls), 8)
        first = next(i for i, (_, p) in enumerate(self.calls) if p)
        self.assertEqual(sum(",readonly" in a for c, _ in self.calls[:first] for a in c), 4)

    def test_write_failure_keeps_original_error_even_when_helper_cleanup_fails(self):
        def failed(args, source=None):
            if source:
                raise PermissionError("private secret employee")
            if args[1] == "rm":
                raise OSError("cleanup private secret")
            return self.docker(args, source)

        with self.assertRaises(PermissionError):
            storage.helper(self.image, "api", storage.EXTRACT, self.root / "api.tar", failed)

    def test_bad_last_archive_nonempty_volume_and_wrong_mount_refuse_all_writes(self):
        for defect in ("traversal", "device", "link-child", "nonempty", "mount"):
            with self.subTest(defect=defect):
                self.packet(self.root / "wechat.tar", defect)
                self.bad_volume = "wechat" if defect == "nonempty" else ""
                self.items["wechat"]["Mounts"][0]["Type"] = (
                    "bind" if defect == "mount" else "volume"
                )
                self.calls.clear()
                with self.assertRaises(storage.CollectionError) as error:
                    storage.restore("recovery", self.manifest, "pg", self.root, self.docker)
                self.assertEqual(str(error.exception).split(":")[:2], ["存储卷", "preflight"])
                self.assertFalse(any(p for _, p in self.calls))
