"""发布前置检查及私有状态记录；迁移开始前持久标记数据库恢复要求。"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backup import private
from restore_postgres import command
from restore_workspace import values

IMAGE_KEYS = {
    "EB_POLICY_SYNC_IMAGE": "policy-sync",
    "EB_KB_GATEWAY_IMAGE": "kb-gateway",
    "EB_FRAPPE_MCP_IMAGE": "frappe-mcp",
}
PIN = (
    r"ghcr\.io/[a-z0-9_.-]+/enterprise-base-"
    r"(?:policy-sync|kb-gateway|frappe-mcp)@sha256:[a-f0-9]{64}"
)
SHA = r"[a-f0-9]{40}"
PHASES = {
    "backed-up",
    "applying",
    "migration-started",
    "ready",
    "failed",
    "rollback-started",
    "rolled-back",
    "rollback-failed",
}


def images(config):
    result = {k: v for k, v in config.items() if k.endswith("_IMAGE") and v}
    if set(result) != set(IMAGE_KEYS) or any(not re.fullmatch(PIN, v) for v in result.values()):
        raise ValueError("服务镜像须为三项已知服务的固定摘要")
    for key, suffix in IMAGE_KEYS.items():
        if not result[key].split("@", 1)[0].endswith("-" + suffix):
            raise ValueError("服务镜像对应关系无效")
    return result


def plan(repository, revision, env, run=command, main_ref="refs/remotes/origin/main"):
    """调用方须先刷新 main 引用；镜像白名单取自已合入 main 的目标版本表。"""
    repository, env = Path(repository).resolve(), Path(env).absolute()
    if not re.fullmatch(SHA, revision):
        raise ValueError("发布目标必须为完整提交号")
    secret = repository / "deploy/secrets"
    if not env.resolve().is_relative_to(secret) or any(p.is_symlink() for p in (env, *env.parents)):
        raise ValueError("发布配置必须在当前部署 secrets 内")
    private(secret, True)
    private(env)
    git = ["git", "-C", str(repository)]
    if run([*git, "status", "--porcelain", "--untracked-files=all"]).strip():
        raise ValueError("发布工作区必须干净")
    current = run([*git, "rev-parse", "HEAD"]).decode().strip()
    if not re.fullmatch(SHA, current) or revision == current:
        raise ValueError("目标提交必须不同于当前提交")
    run([*git, "merge-base", "--is-ancestor", revision, main_ref])
    run([*git, "merge-base", "--is-ancestor", current, main_ref])

    def read(commit, name):
        return run([*git, "show", commit + ":deploy/" + name]).decode()

    old = json.loads(read(current, "upstream.lock.json"))
    new = json.loads(read(revision, "upstream.lock.json"))
    # 只接受目标 main 明确锁定的三项服务镜像，不从宿主机变量挑选新镜像。
    pins = dict(
        line.split("=", 1)
        for line in read(revision, ".env.example").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    old_pins = dict(
        line.split("=", 1)
        for line in read(current, ".env.example").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    config = values(env)
    project = config["EB_PROJECT"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
        raise ValueError("发布项目名无效")
    if images(config) != images(old_pins):
        raise ValueError("当前镜像与当前 main 锁定配置不符")
    return dict(
        project=project,
        before=dict(
            revision=current,
            images=images(values(env)),
            frappe_image=old["frappe_image"],
            dify_source=old["sources"]["dify"]["commit"],
        ),
        target=dict(
            revision=revision,
            images=images(pins),
            frappe_image=new["frappe_image"],
            dify_source=new["sources"]["dify"]["commit"],
        ),
    )


def write(path, record):
    """原子写入；崩溃时保留上一份完整状态，文件始终为 600。"""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("发布记录不能是链接")
    if path.exists():
        private(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(json.dumps(record, ensure_ascii=False).encode())
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def create(deploy, release_plan, archive, running_images):
    """操作锁内调用；加密备份另存，不会被每日备份保留策略删除。"""
    deploy, archive = Path(deploy), Path(archive)
    private(archive)
    before, target = release_plan["before"], release_plan["target"]
    if set(release_plan) != {"before", "target", "project"} or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{0,62}", release_plan["project"]
    ):
        raise ValueError("发布计划包含未知字段")
    for item in (before, target):
        if set(item) != {"revision", "images", "frappe_image", "dify_source"}:
            raise ValueError("版本记录包含未知字段")
        if not re.fullmatch(SHA, item["revision"]):
            raise ValueError("版本提交号无效")
        images(item["images"])
        if (
            not re.fullmatch(SHA, item["dify_source"])
            or not re.fullmatch(r"enterprise-base/frappe:[A-Za-z0-9_.-]+", item["frappe_image"])
            or item["frappe_image"].endswith(":latest")
        ):
            raise ValueError("Frappe/Dify 版本记录无效")
    # Docker inspect 的环境变量和挂载信息不得进入发布记录。
    sanitized = {}
    for service, image in running_images.items():
        if not re.fullmatch(r"(?:-hr|-dify)?/[a-z0-9_-]+", service):
            raise ValueError("组件名无效")
        identity = image["image_id"]
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
            raise ValueError("运行镜像 ID 无效")
        sanitized[service] = identity
    root = deploy / "data/releases"
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("发布目录不能是链接")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    private(root, True)
    now = datetime.now(UTC)
    folder = root / (now.strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12])
    folder.mkdir(mode=0o700)
    destination = folder / "before.tar.age"
    try:
        with archive.open("rb") as source, destination.open("xb") as output:
            os.fchmod(output.fileno(), 0o600)
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        with destination.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        record = dict(
            schema=1,
            created_at=now.isoformat(),
            **release_plan,
            backup="before.tar.age",
            backup_sha256=digest,
            running_images=sanitized,
            phase="backed-up",
            restore_database=False,
        )
        write(folder / "release.json", record)
        return folder / "release.json"
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def load(path):
    path = Path(path)
    private(path)
    record = json.loads(path.read_text())
    if record.get("schema") != 1 or record.get("phase") not in PHASES:
        raise ValueError("发布记录格式无效")
    archive = path.parent / "before.tar.age"
    if record.get("backup") != archive.name:
        raise ValueError("发布备份路径无效")
    private(archive)
    with archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != record["backup_sha256"]:
            raise ValueError("发布备份摘要不符")
    return record


def mark(path, phase):
    if phase not in PHASES:
        raise ValueError("发布阶段无效")
    record = load(path)
    # 标记必须先于 bench migrate；即使迁移中途失败也不能只回退镜像。
    record["restore_database"] |= phase == "migration-started"
    record["phase"] = phase
    write(path, record)
    return record
