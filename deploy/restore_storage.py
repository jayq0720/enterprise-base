"""将 Dify 文件、插件、证书和会话写入隔离项目空卷，不启动业务服务。"""

import json
import re
import tarfile
from pathlib import Path, PurePosixPath

import restore_postgres as pg
from backup import private
from backup_collect import LABEL, CollectionError

VOLUMES = (
    ("-dify", "api", "/app/api/storage"),
    ("-dify", "plugin_daemon", "/app/storage"),
    ("", "caddy", "/data"),
    ("", "wechat", "/app/data"),
)
EMPTY = 'test -z "$(find /restore -mindepth 1 ! -type d -print -quit)"'
EXTRACT = [
    "tar",
    "-xpf",
    "-",
    "--numeric-owner",
    "--same-owner",
    "--delay-directory-restore",
    "-C",
    "/restore",
]


def archive(path):
    """内层 Docker tar 不在宿主机展开；允许插件环境的链接，拒绝沿链接写文件。"""
    private(path)
    entries: dict[PurePosixPath, tarfile.TarInfo] = {}
    links, hardlinks, size = set(), [], 0
    with tarfile.open(path, "r:") as package:
        for item in package:
            name = PurePosixPath(item.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in item.name
                or name in entries
                or len(entries) >= 1_000_000
                or item.type
                not in (
                    tarfile.REGTYPE,
                    tarfile.AREGTYPE,
                    tarfile.DIRTYPE,
                    tarfile.SYMTYPE,
                    tarfile.LNKTYPE,
                )
                or (name == PurePosixPath(".") and not item.isdir())
            ):
                raise ValueError("卷归档路径、数量或类型无效")
            entries[name] = item
            if item.isfile():
                size += item.size
            if item.issym() or item.islnk():
                links.add(name)
            if item.islnk():
                hardlinks.append(PurePosixPath(item.linkname))
    if size > path.stat().st_size or any(p in links for n in entries for p in n.parents):
        raise ValueError("卷归档包含稀疏扩张或链接下的文件")
    if any(n not in entries or not entries[n].isfile() for n in hardlinks):
        raise ValueError("硬链接必须指向归档内普通文件")


def helper(image, volume, args, source=None, run=pg.command, readonly=False):
    """只挂一个目标卷；无网络、无业务环境变量，输出由调用方收集且不打印。"""
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*", volume
    ):
        raise ValueError("恢复镜像或卷名无效")
    mount = f"type=volume,src={volume},dst=/restore,volume-nocopy" + (
        ",readonly" if readonly else ""
    )
    identity = (
        run(
            [
                "docker",
                "create",
                "-i",
                "--network",
                "none",
                "--log-driver",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "FOWNER",
                "--cap-add",
                "DAC_OVERRIDE",
                "--security-opt",
                "no-new-privileges",
                "--no-healthcheck",
                "--mount",
                mount,
                "--entrypoint",
                args[0],
                image,
                *args[1:],
            ]
        )
        .decode()
        .strip()
    )
    succeeded = False
    try:
        result = run(["docker", "start", "-a", "-i", identity], source)
        succeeded = True
        return result
    finally:
        try:
            run(["docker", "rm", "-f", "-v", identity])
        except Exception:
            if succeeded:
                raise


def destination(project, manifest, suffix, service, path, run):
    ids = (
        run(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=" + LABEL + "project=" + project + suffix,
                "--filter",
                "label=" + LABEL + "service=" + service,
            ]
        )
        .decode()
        .split()
    )
    items = json.loads(run(["docker", "inspect", *ids])) if ids else []
    items = [
        i for i in items if i["Config"]["Labels"].get(LABEL + "oneoff", "false").lower() != "true"
    ]
    if len(items) != 1:
        raise ValueError("卷恢复需要唯一目标容器")
    item = items[0]
    labels, state = item["Config"]["Labels"], item["State"]
    if (
        labels.get(LABEL + "project") != project + suffix
        or labels.get(LABEL + "service") != service
        or state["Running"]
        or state.get("Paused")
        or state.get("Restarting")
        or item["Image"] != manifest["images"][suffix + "/" + service]["image_id"]
    ):
        raise ValueError("目标服务身份、版本或停止状态不符")
    mounts = [m for m in item["Mounts"] if m["Destination"] == path]
    if len(mounts) != 1 or mounts[0]["Type"] != "volume" or not mounts[0]["RW"]:
        raise ValueError("目标路径必须是可写命名卷")
    volume = mounts[0]["Name"]
    info = json.loads(run(["docker", "volume", "inspect", volume]))[0]
    if (
        info["Name"] != volume
        or (info.get("Labels") or {}).get(LABEL + "project") != project + suffix
    ):
        raise ValueError("目标卷不属于新项目")
    return volume


def restore(project, manifest, postgres, payload, run=pg.command):
    """必须在 restore_guard.prepared 锁内调用；全部卷预检成功后才开始写入。"""
    step = "preflight"
    try:
        payload = Path(payload)
        private(payload, directory=True)
        pg.target(project, manifest, postgres, run)
        image = manifest["images"]["-hr/backend"]["image_id"]
        plan = []
        for suffix, service, path in VOLUMES:
            if service == "wechat" and "/wechat" not in manifest["images"]:
                continue
            source = payload / (service + ".tar")
            archive(source)
            volume = destination(project, manifest, suffix, service, path, run)
            helper(image, volume, ["sh", "-ec", EMPTY], run=run, readonly=True)
            plan.append((service, source, volume))
        if len({v for _, _, v in plan}) != len(plan):
            raise ValueError("组件不能共用恢复卷")
        for service, source, volume in plan:
            step = service
            helper(image, volume, EXTRACT, source, run)
    except Exception as error:
        raise CollectionError(f"存储卷:{step}:{type(error).__name__}") from None
