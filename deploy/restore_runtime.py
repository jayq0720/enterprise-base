"""完整隔离恢复入口：校验、准备目录、原生数据恢复、有限启动及失败停服。"""

import http.client
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import restore_postgres as pg
import restore_qdrant as qd
import restore_storage as storage
import restore_workspace as ws
from backup_collect import CollectionError

ROOT = Path(__file__).resolve().parent
MARK = "io.enterprise-base.restore"
HOLD = {
    "worker",
    "worker_beat",
    "queue-short",
    "queue-long",
    "scheduler",
    "configurator",
    "create-site",
    "init_permissions",
    "wechat",
    "policy-sync",
    "kb-gateway",
    "frappe-mcp",
}


class Target:
    def __init__(self, deploy, project, run=pg.command):
        self.deploy, self.project, self.run = Path(deploy), project, run
        self.operation, self.step = uuid4().hex, "准备"
        self.models = {
            s: json.loads((self.deploy / f"data/restore-{s}.json").read_text())
            for s in ("edge", "dify", "hr")
        }
        keys = set(ws.values(self.deploy / "secrets/.env")) | set(
            ws.values(self.deploy / "secrets/dify.env")
        )
        self.env = {
            k: v for k, v in os.environ.items() if k not in keys and not k.startswith("COMPOSE_")
        }
        for stack, model in self.models.items():
            for service in model["services"].values():
                service.setdefault("labels", {})[MARK] = self.operation
            ws.write(self.deploy / f"data/restore-{stack}.json", json.dumps(model))

    def call(self, args, source=None, **kwargs):
        try:
            return self.run(args, source, **kwargs)
        except Exception as error:
            raise CollectionError(f"{self.step}:{type(error).__name__}") from None

    def compose(self, stack, *args):
        return self.call(
            [
                "docker",
                "compose",
                "--project-name",
                self.models[stack]["name"],
                "--env-file",
                str(self.deploy / "secrets/.env"),
                "-f",
                str(self.deploy / f"data/restore-{stack}.json"),
                "--profile",
                "*",
                *args,
            ],
            env=self.env,
        )

    def ids(self, stack, service=None):
        args = [
            "docker",
            "ps",
            "-aq",
            # PostgreSQL/Qdrant 恢复要求 64 位完整容器 ID。
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=" + self.models[stack]["name"],
            "--filter",
            "label=" + MARK + "=" + self.operation,
        ]
        if service:
            args += ["--filter", "label=com.docker.compose.service=" + service]
        return self.call(args).decode().split()

    def container(self, stack, service):
        ids = self.ids(stack, service)
        if len(ids) != 1:
            raise CollectionError(self.step + ":MissingContainer")
        return ids[0]

    def start(self, stack, services):
        if services:
            self.compose(
                stack,
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--wait-timeout",
                "600",
                *services,
            )

    def stop(self):
        failures = []
        for stack in self.models:
            try:
                identities = self.ids(stack)
                if identities:
                    self.call(["docker", "stop", "--time", "60", *identities])
            except Exception:
                failures.append(stack)
        return failures

    def frappe(self, stage, sites, primary, port):
        backend = self.container("hr", "backend")
        folder = "/tmp/eb-restore-" + self.operation
        config = ws.values(self.deploy / "secrets/.env")
        env = {
            **self.env,
            "EB_RESTORE_DB_ROOT_PASSWORD": config["EB_FRAPPE_DB_PASSWORD"],
            "EB_RESTORE_ADMIN_PASSWORD": config["EB_FRAPPE_ADMIN_PASSWORD"],
        }
        prefix = ["docker", "exec", "--workdir", "/home/frappe/frappe-bench"]
        credentialed = [
            *prefix,
            "--env",
            "EB_RESTORE_DB_ROOT_PASSWORD",
            "--env",
            "EB_RESTORE_ADMIN_PASSWORD",
            backend,
        ]
        succeeded = False
        try:
            self.call([*prefix, backend, "mkdir", "-m", "700", folder])
            self.call(["docker", "cp", str(stage / "sites"), backend + ":" + folder + "/input"])
            for name in (
                "restore_frappe.py",
                "restore_guard.py",
                "backup.py",
                "backup_bundle.py",
                "backup_collect.py",
            ):
                self.call(["docker", "cp", str(ROOT / name), backend + ":" + folder + "/" + name])
            self.call(
                [
                    "docker",
                    "exec",
                    "--user",
                    "root",
                    backend,
                    "chown",
                    "-R",
                    # 镜像里 frappe 用户的主组是 root，没有 frappe 组。
                    "frappe:",
                    folder,
                ]
            )
            for index, site in enumerate(sites):
                self.call(
                    [
                        *credentialed,
                        "env/bin/python",
                        folder + "/restore_frappe.py",
                        site,
                        folder + f"/input/{index}",
                    ],
                    env=env,
                )
            url = f"http://hr.{config['EB_DOMAIN']}:{port}"
            self.call(
                [*prefix, backend, "bench", "--site", primary, "set-config", "host_name", url]
            )
            succeeded = True
        finally:
            try:
                self.call(["docker", "exec", "--user", "root", backend, "rm", "-rf", folder])
            except Exception:
                try:
                    self.call(["docker", "rm", "-f", backend])
                except Exception:
                    print("恢复:容器暂存清理失败，需人工清理", file=sys.stderr)
                if succeeded:
                    raise

    def restore(self, manifest, stage, sites, primary, port):
        selected = {}
        for stack, suffix in (("edge", ""), ("dify", "-dify"), ("hr", "-hr")):
            selected[stack] = [
                n for n in self.models[stack]["services"] if suffix + "/" + n in manifest["images"]
            ]
            if stack == "hr":
                # configurator 按镜像里的 apps 目录生成 apps.txt，必须和 backend 同一镜像；
                # 备份记录的 configurator 是一次性容器，可能还停在 backend 升级前的旧镜像上。
                self.models[stack]["services"]["configurator"].update(
                    image=manifest["images"]["-hr/backend"]["image_id"], pull_policy="never"
                )
                ws.write(self.deploy / "data/restore-hr.json", json.dumps(self.models[stack]))
                if "configurator" not in selected[stack]:
                    selected[stack].append("configurator")
            self.compose(
                stack,
                "up",
                "--no-start",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                *selected[stack],
            )
        self.step = "数据库启动"
        self.start("hr", ["db", "redis-cache", "redis-queue"])
        self.start("dify", ["db_postgres", "redis"])
        database = self.container("dify", "db_postgres")
        self.step = "PostgreSQL"
        pg.restore(self.project, manifest, database, stage / "backup/payload", run=self.call)
        self.step = "Qdrant"
        qd.restore(
            self.project,
            manifest,
            database,
            self.container("dify", "qdrant"),
            stage / "backup/payload/qdrant.snapshot",
            run=self.call,
        )
        self.step = "存储卷"
        storage.restore(self.project, manifest, database, stage / "backup/payload", run=self.call)
        self.step = "Frappe"
        self.compose("hr", "run", "--rm", "--no-deps", "configurator")
        self.start("hr", ["backend"])
        self.frappe(stage, sites, primary, port)
        self.step = "服务启动"
        for stack in ("dify", "hr", "edge"):
            self.start(stack, [n for n in selected[stack] if n not in HOLD])
        self.step = "HTTP"
        verify(port, ws.values(self.deploy / "secrets/.env")["EB_DOMAIN"])


def verify(port, domain):
    for _ in range(60):
        try:
            for host, path in (("hr", "/api/method/ping"), ("dify", "/")):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request("GET", path, headers={"Host": host + "." + domain})
                    response = connection.getresponse()
                    if host == "hr":
                        healthy = (
                            response.status == 200
                            and json.loads(response.read(65536))["message"] == "pong"
                        )
                    else:
                        healthy = response.status in (200, 301, 302, 303, 307, 308)
                    if not healthy:
                        raise ValueError("HTTP 响应不健康")
                finally:
                    connection.close()
            return
        except (OSError, http.client.HTTPException, ValueError, KeyError):
            time.sleep(5)
    raise CollectionError("HTTP:Unhealthy")
