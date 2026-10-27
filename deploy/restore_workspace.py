"""为恢复建立私有 Git 工作目录及隔离配置；不创建或启动 Docker 业务资源。"""

import json
import os
import re
import secrets
import shutil
import socket
import sys
from pathlib import Path

from backup import private
from backup_collect import CollectionError
from restore_guard import names, new_project
from restore_postgres import command
from restore_storage import VOLUMES


def values(path):
    rows = [
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    ]
    result = dict(rows)
    if len(rows) != len(result):
        raise ValueError("配置键重复")
    return result


def write(path, data):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "wb"
    ) as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(data if isinstance(data, bytes) else data.encode())


def copy_secrets(source, destination):
    private(source, True)
    destination.mkdir(mode=0o700)
    for path in sorted(source.rglob("*")):
        private(path, path.is_dir())
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(mode=0o700)
        else:
            content = path.read_bytes()
            if b"AGE-SECRET-KEY-" in content:
                raise ValueError("解密私钥不能复制到目标服务器配置")
            write(target, content)


def isolated(model, suffix, project, manifest, primary, port):
    """模型来自原生 compose config；只写入新目录，不修改备份版本的源文件。"""
    if model["name"] != project + suffix:
        raise ValueError("Compose 项目名未隔离")
    for volume in model.get("volumes", {}).values():
        if volume.get("external") or not volume["name"].startswith(project + suffix + "_"):
            raise ValueError("恢复不能接管外部或旧卷")
    for network in model.get("networks", {}).values():
        if not (
            network["name"].startswith(project + suffix + "_")
            or network["name"] == project + "_edge"
        ):
            raise ValueError("恢复不能接入旧网络")
    restored = {
        (s, path)
        for suff, s, path in (*VOLUMES, ("-dify", "qdrant", "/qdrant/storage"))
        if suff == suffix
    }
    sources = {
        v["source"]
        for name, svc in model["services"].items()
        for v in svc.get("volumes", [])
        if v["type"] == "volume" and (name, v["target"]) in restored
    }
    for name, service in model["services"].items():
        service["restart"] = "no"
        record = manifest["images"].get(suffix + "/" + name)
        if record:
            service.update(image=record["image_id"], pull_policy="never")
        for mount in service.get("volumes", []):
            if mount["type"] == "volume" and mount["source"] in sources:
                mount.setdefault("volume", {})["nocopy"] = True
        for key, value in service.get("environment", {}).items():
            if suffix == "-dify" and isinstance(value, str):
                for scheme in ("http", "ws"):
                    base = f"{scheme}://dify.{project.replace('_', '-').rstrip('-')}.localhost"
                    if value.startswith(base):
                        service["environment"][key] = value.replace(base, base + ":" + str(port), 1)
        if suffix == "" and name == "caddy":
            service["ports"] = [
                dict(target=80, published=str(port), host_ip="127.0.0.1", protocol="tcp")
            ]
    if suffix == "-hr":
        model["services"]["frontend"]["environment"]["FRAPPE_SITE_NAME_HEADER"] = primary
    return model


def create(repository, manifest, source, sites, project, destination, port, run=command):
    """由 restore_guard.prepared 持锁调用；失败删除本次新建的明文目录。"""
    repository, destination, source = (
        Path(repository).resolve(),
        Path(destination).absolute(),
        Path(source),
    )
    revision = manifest["revision"]
    if not re.fullmatch(r"[a-f0-9]{40}", revision) or not 1024 <= port <= 65535:
        raise ValueError("提交号或恢复端口无效")
    new_project(project, manifest["project"], destination, run)
    if not destination.is_relative_to(repository / "deploy/data/restores"):
        raise ValueError("恢复目录必须在当前仓库忽略的 deploy/data/restores 内")
    sites = names(sites, r"[A-Za-z0-9][A-Za-z0-9_.-]*")
    private(source, True)
    private(source / ".env")
    config = values(source / ".env")
    if config.get("EB_PROJECT") != manifest["project"]:
        raise ValueError("备份密钥配置与清单项目不一致")
    primary = config.get("EB_RESTORE_PRIMARY_SITE") or "hr." + config["EB_DOMAIN"]
    if primary not in sites:
        if len(sites) != 1:
            raise ValueError("无法确定多个站点中的主站点")
        primary = sites[0]
    # 精确镜像必须事先按手册准备；不从可能已经变化的 tag 偷换镜像。
    for record in manifest["images"].values():
        image = record["image_id"]
        if (
            not re.fullmatch(r"sha256:[a-f0-9]{64}", image)
            or run(["docker", "image", "inspect", "--format", "{{.Id}}", image]).decode().strip()
            != image
        ):
            raise ValueError("缺少备份中的精确镜像")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    git = ["git", "-C", str(repository), "-c", "core.hooksPath=/dev/null"]
    if run([*git, "cat-file", "-t", revision]).strip() != b"commit":
        raise ValueError("缺少备份的代码提交")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    owned = False
    try:
        run([*git, "worktree", "add", "--detach", str(destination), revision])
        owned = True
        destination.chmod(0o700)
        deploy = destination / "deploy"
        for path in ("deploy/secrets/.env", "deploy/data/restore-edge.json"):
            run(["git", "-C", str(destination), "check-ignore", "-q", path])
        copy_secrets(source, deploy / "secrets")
        config.update(
            EB_PROJECT=project,
            EB_DOMAIN=project.replace("_", "-").rstrip("-") + ".localhost",
            EB_SCHEME="http",
            EB_BIND="127.0.0.1",
            EB_ADMIN_ALLOW_CIDRS="127.0.0.1/32",
            EB_WECOM_CALLBACK_MODE="bridge",
            EB_BACKUP_DIR="data/backups",
            EB_RESTORE_PRIMARY_SITE=primary,
        )
        for key in ("EB_FRAPPE_DB_PASSWORD", "EB_FRAPPE_ADMIN_PASSWORD", "DB_PASSWORD"):
            config[key] = secrets.token_urlsafe(32)
        write(deploy / "secrets/.env", "".join(f"{k}={v}\n" for k, v in config.items()))
        clean = {
            k: v for k, v in os.environ.items() if k not in config and not k.startswith("COMPOSE_")
        }
        run([sys.executable, str(deploy / "prepare.py"), str(deploy / "secrets/.env")], env=clean)
        for key in values(deploy / "secrets/dify.env"):
            clean.pop(key, None)
        for name, suffix, path, profiles in (
            ("edge", "", "docker-compose.yml", ["wechat"]),
            ("dify", "-dify", "data/dify/docker/eb-compose.yaml", []),
            (
                "hr",
                "-hr",
                "data/frappe/eb-compose.yaml",
                ["bootstrap", "policy-sync", "hr-assistant"],
            ),
        ):
            env = deploy / ("secrets/dify.env" if suffix == "-dify" else "secrets/.env")
            args = ["docker", "compose", "--env-file", str(env), "-f", str(deploy / path)]
            for profile in profiles:
                args += ["--profile", profile]
            model = isolated(
                json.loads(run([*args, "config", "--format", "json"], env=clean)),
                suffix,
                project,
                manifest,
                primary,
                port,
            )
            write(deploy / f"data/restore-{name}.json", json.dumps(model))
        write(
            deploy / "data/restore-info.json",
            json.dumps(dict(revision=revision, project=project, primary=primary, port=port)),
        )
        return deploy, primary
    except BaseException as error:
        if owned:
            try:
                shutil.rmtree(destination)
                run([*git, "worktree", "remove", "--force", str(destination)])
            except Exception as cleanup:
                if destination.exists():
                    raise CollectionError("恢复目录清理:" + type(cleanup).__name__) from None
        raise CollectionError("恢复目录:" + type(error).__name__) from None
