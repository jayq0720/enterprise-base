"""手动发布及回滚；共享备份锁，迁移回滚要求离线私钥和两次人工确认。"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import backup
import release_runtime as execution
import release_state as state
import rollback_data as data
from restore_postgres import command
from restore_workspace import values

ROOT = Path(__file__).resolve().parent


class DatabaseRestoreRequired(RuntimeError):
    """提示操作者选择离线私钥及覆盖参数，不包含配置值。"""


class InteractiveTerminalRequired(RuntimeError):
    """数据库覆盖确认不接受管道或无人值守任务。"""


def fingerprint(config):
    return hashlib.sha256(
        json.dumps(
            {k: v for k, v in config.items() if not k.endswith("_IMAGE")}, sort_keys=True
        ).encode()
    ).hexdigest()


def images(config):
    path = ROOT / "secrets/.env"
    content = values(path)
    content.update(config)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write("".join(f"{k}={v}\n" for k, v in content.items()))
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def git(*args):
    return command(["git", "-C", str(ROOT.parent), *args])


def verify(revision):
    """先在独立目录验证目标 main，当前服务及密钥不参与验证。"""
    with tempfile.TemporaryDirectory(prefix=".release-check-", dir=ROOT / "data") as work:
        checkout = Path(work) / "checkout"
        added = False
        try:
            git(
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--detach",
                str(checkout),
                revision,
            )
            added = True
            command(["make", "-C", str(checkout / "deploy"), "verify"])
        finally:
            failed = sys.exception() is not None
            if added:
                try:
                    git("worktree", "remove", "--force", str(checkout))
                except Exception:
                    if not failed:
                        raise
                    print("发布:校验目录清理失败", file=sys.stderr)


def failed(record, runtime, mutated, phase):
    if runtime and mutated:
        pending = runtime.stop()
        if pending:
            print("发布:停服失败:" + ",".join(pending), file=sys.stderr)
    if record:
        try:
            state.mark(record, phase)
        except Exception:
            print("发布:失败状态写入失败", file=sys.stderr)


def release(revision):
    record, runtime, mutated = None, None, False
    with backup.operation(wait=backup.BACKUP_LOCK_WAIT), backup.cancellation() as check:
        try:
            print("发布:检查")
            git("fetch", "origin", "main")
            plan = state.plan(ROOT.parent, revision, ROOT / "secrets/.env")
            verify(revision)
            check()
            collector, active, previous = execution.running(ROOT)
            # 基线代码必须与实际运行的App相符，不能把先git pull后的HEAD误记为旧版本。
            label = (
                command(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                        previous["-hr/backend"],
                    ]
                )
                .decode()
                .strip()
            )
            if label != plan["before"]["revision"]:
                raise ValueError("运行App镜像缺少匹配的部署提交标签")
            for key, service in state.IMAGE_KEYS.items():
                name = "-hr/" + service
                if name in active and key not in plan["before"]["images"]:
                    raise ValueError("运行镜像与基线配置不一致")
                if (
                    name in active
                    and command(
                        [
                            "docker",
                            "image",
                            "inspect",
                            "--format",
                            "{{.Id}}",
                            plan["before"]["images"][key],
                        ]
                    )
                    .decode()
                    .strip()
                    != previous[name]
                ):
                    raise ValueError("运行镜像与基线配置不一致")
            print("发布:备份")
            archive = backup.backup(ROOT / "secrets/.env", locked=True)
            record = state.create(ROOT, plan, archive, collector.images, active)
            metadata = state.load(record)
            metadata["config_sha256"] = fingerprint(values(ROOT / "secrets/.env"))
            state.write(record, metadata)
            for identity in set(previous.values()):
                command(
                    [
                        "docker",
                        "tag",
                        identity,
                        "enterprise-base/release-cache:"
                        + record.parent.name
                        + "-"
                        + identity[-12:],
                    ]
                )
            check()
            print("发布:准备镜像")
            git("-c", "core.hooksPath=/dev/null", "checkout", "--detach", revision)
            images(plan["target"]["images"])
            command(["make", "-C", str(ROOT), "build"])
            execution.prepare(ROOT)
            runtime = execution.Runtime(ROOT, plan["project"], active, previous)
            check()
            state.mark(record, "applying")
            mutated = True
            print("发布:启动与迁移")
            runtime.apply(lambda: state.mark(record, "migration-started"))
            check()
            state.mark(record, "ready")
            return record
        except BaseException:
            failed(record, runtime, mutated, "failed")
            raise


def rollback(record=None, identity=None, restore_data=False):
    runtime, mutated = None, False
    with backup.operation(wait=backup.BACKUP_LOCK_WAIT), backup.cancellation() as check:
        print("回滚:检查")
        if record is None:
            candidates = sorted((ROOT / "data/releases").glob("*/release.json"), reverse=True)
            record = next((p for p in candidates if state.load(p)["phase"] != "rolled-back"), None)
        if record is None:
            raise ValueError("没有可回滚的发布记录")
        record = Path(record).absolute()
        if not record.resolve().is_relative_to(ROOT / "data/releases") or any(
            p.is_symlink() for p in (record, *record.parents)
        ):
            raise ValueError("发布记录位置无效")
        metadata = state.load(record)
        current = values(ROOT / "secrets/.env")
        if current["EB_PROJECT"] != metadata["project"] or fingerprint(current) != metadata.get(
            "config_sha256"
        ):
            raise ValueError("部署或密钥已变更，需专项恢复")
        if git("status", "--porcelain", "--untracked-files=all").strip() or git(
            "rev-parse", "HEAD"
        ).decode().strip() not in {metadata["before"]["revision"], metadata["target"]["revision"]}:
            raise ValueError("当前代码不是本次发布版本或工作区不干净")
        needs_data = metadata["restore_database"] or restore_data
        if needs_data and (not restore_data or not identity):
            raise DatabaseRestoreRequired
        if needs_data and not sys.stdin.isatty():
            raise InteractiveTerminalRequired
        try:
            from contextlib import nullcontext

            context = (
                data.prepared(
                    record.parent / metadata["backup"],
                    identity,
                    metadata["project"],
                    metadata["before"]["revision"],
                )
                if needs_data
                else nullcontext(None)
            )
            with context as payload:
                approval = data.confirm(metadata["project"]) if needs_data else None
                check()
                git(
                    "-c",
                    "core.hooksPath=/dev/null",
                    "checkout",
                    "--detach",
                    metadata["before"]["revision"],
                )
                images(metadata["before"]["images"])
                execution.prepare(ROOT)
                runtime = execution.Runtime(
                    ROOT,
                    metadata["project"],
                    set(metadata["running_services"]),
                    metadata["running_images"],
                    pinned=metadata["running_images"],
                )
                check()
                state.mark(record, "rollback-started")
                mutated = True
                if needs_data:
                    identities = data.inventory(metadata["project"])
                    if identities:
                        command(["docker", "stop", "--time", "60", *identities])
                    data.restore(runtime, payload, approval)
                runtime.apply(None)
                check()
                state.mark(record, "rolled-back")
        except BaseException:
            failed(record, runtime, mutated, "rollback-failed")
            raise
