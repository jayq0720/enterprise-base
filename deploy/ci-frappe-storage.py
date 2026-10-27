"""仅临时 CI：用官方 Frappe 镜像实际写入新卷，不连接业务网络。"""

import io
import sys
import tarfile
import tempfile
from pathlib import Path
from uuid import uuid4

from restore_postgres import command
from restore_storage import EMPTY, EXTRACT, archive, helper


def main():
    image = (
        command(["docker", "image", "inspect", "--format", "{{.Id}}", "eb-frappe-contract:0.1.0"])
        .decode()
        .strip()
    )
    volume = "eb-ci-storage-" + uuid4().hex
    command(["docker", "volume", "create", volume])
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "volume.tar"
            with tarfile.open(path, "w") as output:
                item = tarfile.TarInfo("payload")
                item.uid, item.gid, item.mode, item.size = 1000, 1000, 0o600, 9
                output.addfile(item, io.BytesIO(b"synthetic"))
                link = tarfile.TarInfo("python")
                link.type, link.linkname = tarfile.SYMTYPE, "/usr/local/bin/python"
                output.addfile(link)
            path.chmod(0o600)
            archive(path)
            helper(image, volume, ["sh", "-ec", EMPTY], readonly=True)
            helper(image, volume, EXTRACT, path)
            verify = (
                "import os,pathlib; p=pathlib.Path('/restore/payload'); "
                "assert p.read_bytes()==b'synthetic'; "
                "s=p.stat(); assert (s.st_uid,s.st_gid,s.st_mode&0o777)==(1000,1000,0o600); "
                "assert os.readlink('/restore/python')=='/usr/local/bin/python'"
            )
            helper(
                image,
                volume,
                ["/home/frappe/frappe-bench/env/bin/python", "-c", verify],
                readonly=True,
            )
        print("存储卷原生演练：空卷恢复、内容、UID/GID、600 权限与插件符号链接通过。")
    finally:
        command(["docker", "volume", "rm", volume])


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("存储卷原生演练:失败:" + type(error).__name__)
        sys.exit(1)
