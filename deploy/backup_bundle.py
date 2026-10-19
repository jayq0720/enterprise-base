"""备份包格式：固定 age、加密清单、校验后解包和每日/每周保留。"""

import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent


def sha(stream):
    return hashlib.file_digest(stream, "sha256").hexdigest()


def read_member(archive, member):
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError("归档项不是普通文件。")
    return stream


def tool(name):
    lock = json.loads((ROOT / "age.lock.json").read_text())
    arch = "amd64" if platform.machine() == "x86_64" else platform.machine()
    tag = f"{platform.system().lower()}-{arch}"
    spec = lock["platforms"][tag]
    spec["files"] = {item["path"]: item["sha256"] for item in spec["files"]}
    folder = ROOT / "data/tools" / ("age-" + lock["version"]) / tag
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / name
    if all(
        (folder / n).is_file()
        and not (folder / n).is_symlink()
        and hashlib.sha256((folder / n).read_bytes()).hexdigest() == expected
        for n, expected in spec["files"].items()
    ):
        return str(target)
    with tempfile.TemporaryFile() as archive:
        try:
            with urllib.request.urlopen(spec["url"], timeout=60) as response:
                shutil.copyfileobj(response, archive)
        except OSError as error:
            # 国内服务器常连不上 GitHub 发布文件：可离线放置官方文件，摘要一致即直接使用。
            raise RuntimeError(
                f"无法下载 age；离线环境请把官方 {spec['url'].rsplit('/', 1)[-1]} 里的 "
                f"{'、'.join(spec['files'])} 放到 {folder}，摘要须与 age.lock.json 一致。"
            ) from error
        archive.seek(0)
        if sha(archive) != spec["sha256"]:
            raise ValueError("age 官方包摘要不符。")
        archive.seek(0)
        with tarfile.open(fileobj=archive) as package:
            for filename, expected in spec["files"].items():
                member = package.getmember("age/" + filename)
                if not member.isfile():
                    raise ValueError("age 包含不支持的文件类型。")
                data = read_member(package, member).read()
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("age 工具文件摘要不符。")
                with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temp:
                    temp.write(data)
                    temp.flush()
                    os.fchmod(temp.fileno(), 0o600 if filename == "LICENSE" else 0o700)
                os.replace(temp.name, folder / filename)
    return str(target)


def safe_name(name):
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and path.as_posix() == name
    )


def pack(source, output, recipient, metadata):
    source, output = Path(source), Path(output)
    if (
        source.is_symlink()
        or not source.is_dir()
        or output.resolve().is_relative_to(source.resolve())
    ):
        raise ValueError("源必须是独立目录，输出不能放在源内。")
    if set(metadata) != {"project", "revision", "images", "versions"}:
        raise ValueError("清单仅接受项目、提交、镜像和版本元数据。")
    entries: list[dict] = []
    for path in sorted(source.rglob("*")):
        name = path.relative_to(source).as_posix()
        if path.is_symlink() or not safe_name(name) or name == "manifest.json":
            raise ValueError("源包含不安全路径或保留文件名。")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_nlink != 1 or len(entries) >= 9999:
            raise ValueError("源只能包含普通文件。")
        with path.open("rb") as stream:
            entries.append(dict(path=name, size=path.stat().st_size, sha256=sha(stream)))
    manifest = dict(metadata, schema=1, files=entries)
    manifest["created_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    with tempfile.TemporaryDirectory(prefix=".bundle-", dir=output.parent) as work:
        partial = Path(work) / "encrypted.age"
        with partial.open("xb") as encrypted:
            partial.chmod(0o600)
            proc = subprocess.Popen(
                [tool("age"), "-r", recipient],
                stdin=subprocess.PIPE,
                stdout=encrypted,
                stderr=subprocess.DEVNULL,
            )
            assert proc.stdin is not None
            try:
                with tarfile.open(fileobj=proc.stdin, mode="w|") as archive:
                    for entry in entries:
                        path = source / entry["path"]
                        info = archive.gettarinfo(path, arcname=entry["path"])
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        info.mode = 0o600
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                        with path.open("rb") as stream:
                            if (
                                path.stat().st_size != entry["size"]
                                or sha(stream) != entry["sha256"]
                            ):
                                raise ValueError("打包期间文件发生变化。")
                    content = json.dumps(manifest, ensure_ascii=False).encode()
                    if len(content) > 4 * 1024 * 1024:
                        raise ValueError("清单过大。")
                    info = tarfile.TarInfo("manifest.json")
                    info.size, info.mode = len(content), 0o600
                    archive.addfile(info, io.BytesIO(content))
                proc.stdin.close()
                if proc.wait() != 0:
                    raise ValueError("age 加密失败。")
                encrypted.flush()
                os.fsync(encrypted.fileno())
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                proc.stdin.close()
        os.link(partial, output)  # 原子发布且拒绝覆盖已有包。
    return manifest


def unpack(source, destination, identity, work_dir=None):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("只允许解包到不存在的新目录。")
    # 解密明文默认放在目标同一分区，不用可能是内存盘、空间小的 /tmp。
    work = Path(work_dir) if work_dir is not None else destination.parent
    if work.is_symlink() or not work.is_dir():
        raise ValueError("解密临时目录必须是已存在的普通目录。")
    with tempfile.TemporaryFile(dir=work) as clear, open(source, "rb") as encrypted:
        subprocess.run(
            [tool("age"), "-d", "-i", str(identity)],
            stdin=encrypted,
            stdout=clear,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        clear.seek(0)
        with tarfile.open(fileobj=clear, mode="r:") as archive:
            members: dict[str, tarfile.TarInfo] = {}
            for member in archive:
                if (
                    len(members) >= 10000
                    or not member.isfile()
                    or not safe_name(member.name)
                    or member.name in members
                ):
                    raise ValueError("备份包含不安全路径、链接或重复项。")
                members[member.name] = member
            info = members.pop("manifest.json")
            if info.size > 4 * 1024 * 1024:
                raise ValueError("清单过大。")
            manifest = json.load(read_member(archive, info))
            entries = {entry["path"]: entry for entry in manifest["files"]}
            if (
                manifest["schema"] != 1
                or set(entries) != set(members)
                or len(entries) != len(manifest["files"])
            ):
                raise ValueError("备份清单版本或文件列表不符。")
            for name, entry in entries.items():
                member = members[name]
                if (
                    member.size != entry["size"]
                    or sha(read_member(archive, member)) != entry["sha256"]
                ):
                    raise ValueError("备份文件大小或 SHA256 不符。")
            destination.mkdir(mode=0o700)
            try:
                for name, member in members.items():
                    target = destination / name
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with os.fdopen(
                        os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
                        "wb",
                    ) as stream:
                        shutil.copyfileobj(read_member(archive, member), stream)
            except BaseException:
                shutil.rmtree(destination)
                raise
    return manifest


def backup_name(project, now):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project) or now.tzinfo is None:
        raise ValueError("项目名或时区无效。")
    return f"{project}-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.tar.age"


def prune(folder, project, now, daily=7, weekly=4):
    backup_name(project, now)
    if not 1 <= daily <= 365 or not 1 <= weekly <= 104:
        raise ValueError("保留天数或周数超出范围。")
    pattern = re.compile(re.escape(project) + r"-(\d{8}T\d{6}Z)-[a-f0-9]{8}\.tar\.age")
    candidates = []
    for path in Path(folder).iterdir():
        match = pattern.fullmatch(path.name)
        if match and path.is_file() and not path.is_symlink():
            stamp = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            candidates.append((stamp, path))
    weeks: set[tuple[int, int]] = set()
    removed = []
    for stamp, path in sorted(candidates, reverse=True):
        week = stamp.astimezone(ZoneInfo("Asia/Shanghai")).isocalendar()[:2]
        keep_week = (
            stamp >= now - timedelta(weeks=weekly) and week not in weeks and len(weeks) < weekly
        )
        weeks.add(week)
        if stamp < now - timedelta(days=daily) and not keep_week:
            path.unlink()
            removed.append(path.name)
    return removed
