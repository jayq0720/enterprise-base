"""恢复前置校验；解密、原生数据写入和正式覆盖由后续入口协调。"""

import json
import os
import re
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import backup
import backup_bundle as bundle
from backup_collect import command

SITE_FILES = {"database.sql.gz", "files.tar", "private-files.tar", "site_config.json"}


@contextmanager
def failure(on_error):
    try:
        yield
    except BaseException:
        if on_error:
            on_error()
        raise


@contextmanager
def prepared(archive, identity, project, directory, run=command, on_error=None):
    """持有操作锁直至调用方完成；任何退出路径清理解密内容。"""
    with backup.operation(), backup.cancellation() as check, failure(on_error):
        backup.private(Path(identity))
        backup.private(Path(archive))
        with tempfile.TemporaryDirectory(
            prefix=".restore-clear-", dir=backup.ROOT / "data"
        ) as work:
            stage = Path(work)
            manifest = bundle.unpack(archive, stage / "backup", identity)
            check()
            sites, databases = layout(stage / "backup", manifest)
            new_project(project, manifest["project"], directory, run)
            extract_sites(stage / "backup/payload/frappe.tar", stage / "sites", sites)
            for index in range(len(sites)):
                if not isinstance(
                    json.loads((stage / f"sites/{index}/site_config.json").read_text()), dict
                ):
                    raise ValueError("站点配置不是对象")
            check()
            yield manifest, stage, sites, databases
            check()


def names(value, pattern):
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 1000
        or any(not isinstance(v, str) or not re.fullmatch(pattern, v) for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("备份索引无效")
    return value


def layout(folder, manifest):
    """只在 backup_bundle.unpack 验证全部 SHA256 后使用。"""
    folder = Path(folder)
    sites = names(
        json.loads((folder / "payload/sites.json").read_text()), r"[A-Za-z0-9][A-Za-z0-9_.-]*"
    )
    databases = names(
        json.loads((folder / "payload/databases.json").read_text()), r"[A-Za-z0-9_][A-Za-z0-9_-]*"
    )
    images = manifest["images"]
    if not isinstance(images, dict):
        raise ValueError("镜像清单无效")
    required = {
        "frappe.tar",
        "api.tar",
        "plugin_daemon.tar",
        "caddy.tar",
        "qdrant.snapshot",
        "sites.json",
        "databases.json",
        "postgres-globals.sql",
    }
    required.update(f"postgres-{n}.dump" for n in range(len(databases)))
    if "/wechat" in images:
        required.add("wechat.tar")
    actual = {p.name for p in (folder / "payload").iterdir()}
    if actual != required or not (folder / "secrets/.env").is_file():
        raise ValueError("备份组件缺失或包含未知负载")
    for name in required:
        path = folder / "payload" / name
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise ValueError("备份负载不是非空普通文件")
    return sites, databases


def new_project(project, source, directory, run=command):
    """拒绝旧容器、卷、网络及名称重叠；本函数不会停止或删除资源。"""
    pattern = r"[a-z0-9][a-z0-9_-]{0,62}"
    if not all(isinstance(v, str) and re.fullmatch(pattern, v) for v in (project, source)):
        raise ValueError("恢复项目名无效")
    targets = {project + suffix for suffix in ("", "-hr", "-dify")}
    sources = {source + suffix for suffix in ("", "-hr", "-dify")}
    directory = Path(directory)
    if (
        targets & sources
        or directory.exists()
        or directory.is_symlink()
        or any(p.is_symlink() for p in directory.parents)
    ):
        raise ValueError("只能恢复到不存在的新项目与新目录")
    for target in sorted(targets):
        label = "label=com.docker.compose.project=" + target
        for args in (["ps", "-aq"], ["volume", "ls", "-q"], ["network", "ls", "-q"]):
            if run(["docker", *args, "--filter", label]).strip():
                raise ValueError("恢复目标已有 Compose 资源")
    # 手工建立、没有 Compose 标签的同名卷/网络也不能被默默接管。
    for kind in ("volume", "network"):
        existing = run(["docker", kind, "ls", "--format", "{{.Name}}"]).decode().split()
        if any(
            name.startswith(target + "_") or name == target
            for name in existing
            for target in targets
        ):
            raise ValueError("恢复目标存在同名卷或网络")


def site_settings(original, target):
    """仅从原配置导入两项解密密钥，数据库连接与口令完全沿用目标。"""
    if not all(isinstance(target.get(k), str) and target[k] for k in ("db_name", "db_password")):
        raise ValueError("目标站点尚未建立独立数据库")
    result = dict(target)
    for key in ("encryption_key", "backup_encryption_key"):
        value = original.get(key)
        if value not in (None, ""):
            if not isinstance(value, str):
                raise ValueError("源站点解密密钥无效")
            result[key] = value
    # 新环境验收前不调度任务、不发邮件或企业微信审批卡片。
    result.update(pause_scheduler=1, mute_emails=1, eb_wecom_cards={"enabled": False})
    return result


def extract_sites(archive, destination, sites):
    """仅展开 Docker cp 的编号目录，附件内层 tar 留给原生 bench 恢复。"""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("站点暂存目录必须不存在")
    expected = {f"{i}/{name}" for i in range(len(sites)) for name in SITE_FILES}
    with tarfile.open(archive, mode="r:") as package:
        members = {}
        count = 0
        for item in package:
            count += 1
            path = PurePosixPath(item.name)
            name = path.as_posix()
            if (
                count > len(sites) * 6 + 1
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in item.name
            ):
                raise ValueError("站点归档路径或数量异常")
            if item.isdir() and name in {".", *(str(i) for i in range(len(sites)))}:
                continue
            if name not in expected or name in members or not item.isfile() or item.size <= 0:
                raise ValueError("站点归档包含缺损文件、重复项或链接")
            members[name] = item
        if set(members) != expected:
            raise ValueError("站点归档不完整")
        destination.mkdir(mode=0o700)
        try:
            for name, item in members.items():
                target = destination / name
                target.parent.mkdir(mode=0o700, exist_ok=True)
                with os.fdopen(
                    os.open(target, os.O_EXCL | os.O_CREAT | os.O_WRONLY, 0o600), "wb"
                ) as output:
                    stream = package.extractfile(item)
                    if stream is None:
                        raise ValueError("站点归档读取失败")
                    with stream:
                        shutil.copyfileobj(stream, output)
        except BaseException:
            shutil.rmtree(destination)
            raise
