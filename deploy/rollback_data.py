"""维护锁及二次确认后的整包数据覆盖恢复；不提供绕过确认的命令参数。"""

import json
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import backup_bundle as bundle
import restore_guard as guard
import restore_postgres as pg
import restore_postgres_existing as existing_pg
import restore_qdrant as qd
import restore_storage as storage
from backup import private
from restore_workspace import values, write

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Approval:
    project: str


def confirm(project, ask=input, terminal=None):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
        raise ValueError("项目名无效")
    if not (sys.stdin.isatty() if terminal is None else terminal):
        raise ValueError("数据库回滚必须在交互终端二次确认")
    print("将丢失发布后产生的数据；恢复发布前数据库、文件和向量快照。")
    if ask("第一次确认：输入目标项目名：") != project:
        raise ValueError("已取消回滚")
    if ask("第二次确认：输入“确认丢失发布后数据”：") != "确认丢失发布后数据":
        raise ValueError("已取消回滚")
    return Approval(project)


@contextmanager
def prepared(archive, identity, project, revision, run=pg.command):
    """须由调用方持有共享操作锁；在停写之前完成解密、清单及镜像检查。"""
    private(Path(archive))
    private(Path(identity))
    with tempfile.TemporaryDirectory(prefix=".rollback-clear-", dir=ROOT / "data") as work:
        stage = Path(work)
        manifest = bundle.unpack(archive, stage / "backup", identity)
        if manifest["project"] != project or manifest["revision"] != revision:
            raise ValueError("发布记录与备份的项目或提交不一致")
        sites, databases = guard.layout(stage / "backup", manifest)
        if {"template0", "template1"} & set(databases):
            raise ValueError("不能覆盖模板数据库")
        guard.extract_sites(stage / "backup/payload/frappe.tar", stage / "sites", sites)
        images = {
            record["image_id"]
            for key, record in manifest["images"].items()
            if key != "-hr/configurator"
        }
        for image in images:
            if (
                not re.fullmatch(r"sha256:[a-f0-9]{64}", image)
                or run(["docker", "image", "inspect", "--format", "{{.Id}}", image])
                .decode()
                .strip()
                != image
            ):
                raise ValueError("缺少发布前精确镜像")
        yield manifest, stage, sites, databases


def inventory(project, run=pg.command):
    result = []
    for suffix in ("", "-hr", "-dify"):
        ids = (
            run(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--no-trunc",
                    "--filter",
                    "label=com.docker.compose.project=" + project + suffix,
                ]
            )
            .decode()
            .split()
        )
        for item in json.loads(run(["docker", "inspect", *ids])) if ids else []:
            labels = item["Config"]["Labels"]
            if labels.get("com.docker.compose.project") != project + suffix:
                raise ValueError("容器项目不符")
            if item["State"]["Running"]:
                if labels.get("com.docker.compose.oneoff", "false").lower() == "true":
                    raise ValueError("存在其它维护任务")
                if labels["com.docker.compose.service"] not in {
                    "db",
                    "db_postgres",
                    "redis",
                    "redis-cache",
                    "redis-queue",
                }:
                    result.append(item["Id"])
    return result


def restore(runtime, data, approval):
    """调用前必须已停下应用；任何失败由总入口继续保持应用停止。"""
    if (
        not isinstance(approval, Approval)
        or approval.project != runtime.project
        or runtime.prefix != "release"
    ):
        raise ValueError("缺少当前项目的明确覆盖确认")
    manifest, stage, sites, databases = data
    if manifest["project"] != runtime.project:
        raise ValueError("覆盖恢复的来源不符")
    if inventory(runtime.project, runtime.call):
        raise ValueError("覆盖前必须停止所有应用和Qdrant")
    for stack, suffix in (("edge", ""), ("dify", "-dify"), ("hr", "-hr")):
        model = runtime.models[stack]
        selected = [name for name in model["services"] if suffix + "/" + name in manifest["images"]]
        for name in selected:
            key = "-hr/backend" if name == "configurator" else suffix + "/" + name
            model["services"][name].update(
                image=manifest["images"][key]["image_id"], pull_policy="never"
            )
        write(runtime.deploy / f"data/release-{stack}.json", json.dumps(model))
        runtime.compose(
            stack, "up", "--no-start", "--no-deps", "--no-build", "--pull", "never", *selected
        )
    runtime.start("hr", ["db", "redis-cache", "redis-queue"])
    runtime.start("dify", ["db_postgres", "redis"])
    database = runtime.container("dify", "db_postgres")
    pg.target(runtime.project, manifest, database, runtime.call, existing=True)
    payload = stage / "backup/payload"
    plans = []
    for suffix, name, path in (*storage.VOLUMES, ("-dify", "qdrant", "/qdrant/storage")):
        if name == "wechat" and "/wechat" not in manifest["images"]:
            continue
        volume = storage.destination(runtime.project, manifest, suffix, name, path, runtime.call)
        if name != "qdrant":
            storage.archive(payload / (name + ".tar"))
        plans.append((suffix, name, volume))
    if len({volume for _, _, volume in plans}) != len(plans):
        raise ValueError("恢复组件不能共用数据卷")
    existing_pg.restore(runtime.project, manifest, database, payload, runtime.call)
    image = manifest["images"]["-hr/backend"]["image_id"]
    for _suffix, name, volume in plans:
        storage.helper(
            image, volume, ["find", "/restore", "-mindepth", "1", "-delete"], run=runtime.call
        )
        if name == "qdrant":
            qd.recover(
                manifest["images"]["-dify/qdrant"]["image_id"],
                volume,
                payload / "qdrant.snapshot",
                runtime.call,
            )
        else:
            storage.helper(image, volume, storage.EXTRACT, payload / (name + ".tar"), runtime.call)
    # 清掉可能引用备份之后记录的旧队列和缓存，避免恢复后执行过期任务。
    config = values(runtime.deploy / "secrets/.env")
    for stack, service in (("hr", "redis-cache"), ("hr", "redis-queue"), ("dify", "redis")):
        env = dict(runtime.env)
        args = ["docker", "exec"]
        if stack == "dify":
            env["REDISCLI_AUTH"] = config["REDIS_PASSWORD"]
            args += ["--env", "REDISCLI_AUTH"]
        runtime.call([*args, runtime.container(stack, service), "redis-cli", "FLUSHALL"], env=env)
    runtime.compose("hr", "run", "--rm", "--no-deps", "configurator")
    runtime.start("hr", ["backend"])
    runtime.frappe(stage, sites, sites[0], 0, existing=True)
