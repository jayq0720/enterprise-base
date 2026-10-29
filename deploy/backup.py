"""P3-G2 完整备份入口；密钥只从服务器私有目录读取。"""

import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import backup_bundle as bundle
from backup_collect import CollectionError, Collector

ROOT = Path(__file__).resolve().parent
BACKUP_LOCK_WAIT = 300


def private(path, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected(info.st_mode)
        or info.st_mode & 0o077
        or info.st_uid != os.getuid()
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("私有文件权限或类型错误")


@contextmanager
def operation(wait=0):
    """备份、恢复和发布共用此锁；不删除锁文件，避免第二把锁。

    wait 秒内锁被占用时每秒重试一次；超时仍抛 BlockingIOError。
    """
    folder = ROOT / "data"
    if folder.is_symlink():
        raise ValueError("data 目录不能是链接")
    folder.mkdir(exist_ok=True)
    with os.fdopen(
        os.open(folder / "operations.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "rb"
    ) as lock:
        private(folder / "operations.lock")
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        yield


@contextmanager
def cancellation():
    """延迟取消到安全边界，不打断原生备份、临时容器清理和服务恢复。"""
    requested = False

    def request(_signum, _frame):
        nonlocal requested
        requested = True

    def check():
        if requested:
            raise InterruptedError

    previous = {sig: signal.signal(sig, request) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield check
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def configuration(path):
    path = Path(path).absolute()
    source = ROOT / "secrets"
    if not path.resolve().is_relative_to(source.resolve()) or any(
        p.is_symlink() for p in [source, *path.parents]
    ):
        raise ValueError("配置必须在服务器 secrets 内")
    private(source, True)
    private(path)
    pairs = [
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    ]
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError("配置键重复")
    project = values["EB_PROJECT"]
    bundle.backup_name(project, datetime.now(UTC))
    recipient = values["EB_BACKUP_RECIPIENT"]
    # 先让固定版本 age 实际验证公钥，不能等停服后才发现填错。
    subprocess.run(
        [bundle.tool("age"), "-r", recipient],
        input=b"",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    daily = int(values.get("EB_BACKUP_DAILY", "7"))
    weekly = int(values.get("EB_BACKUP_WEEKLY", "4"))
    if not 1 <= daily <= 365 or not 1 <= weekly <= 104:
        raise ValueError("保留周期无效")
    target = ROOT / (values.get("EB_BACKUP_DIR") or "data/backups")
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError("备份目录不能是链接")
    target = target.resolve()
    if target.is_relative_to(source.resolve()) or source.resolve().is_relative_to(target):
        raise ValueError("备份目录与 secrets 不能重叠")
    if target.is_relative_to(ROOT.parent) and not target.is_relative_to(
        (ROOT / "data/backups").resolve()
    ):
        raise ValueError("仓库内只允许使用被忽略的 data/backups")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    private(target, True)
    return project, recipient, target, daily, weekly


def copy_secrets(destination):
    source = ROOT / "secrets"
    private(source, True)
    destination.mkdir(mode=0o700)
    for path in sorted(source.rglob("*")):
        target = destination / path.relative_to(source)
        private(path, path.is_dir())
        if path.is_dir():
            target.mkdir(mode=0o700)
        else:
            with path.open("rb") as stream, target.open("xb") as output:
                os.fchmod(output.fileno(), 0o600)
                data = stream.read()
                if b"AGE-SECRET-KEY-" in data:
                    raise ValueError("age 解密私钥必须由 JQ 另存")
                output.write(data)


def backup(env):
    os.umask(0o077)
    # 每 5 分钟的 make health 会短暂持锁；定时备份等它结束，不因撞上而整晚失败。
    with operation(wait=BACKUP_LOCK_WAIT), cancellation() as check:
        project, recipient, target, daily, weekly = configuration(env)
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()
        versions = {
            "age": json.loads((ROOT / "age.lock.json").read_text())["version"],
            "apps": json.loads((ROOT / "apps.lock.json").read_text()),
        }
        check()
        # 同一分区的 700 暂存区；TemporaryDirectory 在所有异常路径删除明文。
        with tempfile.TemporaryDirectory(prefix=".backup-clear-", dir=target) as work:
            stage = Path(work)
            payload = stage / "payload"
            payload.mkdir(mode=0o700)
            copy_secrets(stage / "secrets")
            check()
            images = Collector(project).collect(payload)
            check()
            output = target / bundle.backup_name(project, datetime.now(UTC))
            bundle.pack(
                stage,
                output,
                recipient,
                dict(project=project, revision=revision, images=images, versions=versions),
            )
            check()
        bundle.prune(target, project, datetime.now(UTC), daily, weekly)
        return output


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError
        backup(sys.argv[1])
    except Exception as error:
        message = str(error) if isinstance(error, CollectionError) else type(error).__name__
        print("备份:失败:" + message, file=sys.stderr)
        return 1
    print("备份:成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
