"""已有部署的发布执行部分；调用方负责备份、状态及维护锁。"""

import json
import os
import re
import sys
from pathlib import Path

from backup_collect import KEEP, Collector
from restore_postgres import command
from restore_runtime import Target
from restore_workspace import values, write

MODELS = (
    ("edge", "", "docker-compose.yml", "secrets/.env"),
    ("dify", "-dify", "data/dify/docker/eb-compose.yaml", "secrets/dify.env"),
    ("hr", "-hr", "data/frappe/eb-compose.yaml", "secrets/.env"),
)
HTTP = (
    "import sys,urllib.request;"
    "r=urllib.request.Request(sys.argv[1],headers={'Host':sys.argv[2]});"
    "print(urllib.request.urlopen(r,timeout=10).status)"
)


class Runtime(Target):
    def __init__(self, deploy, project, running, previous, run=command, pinned=None):
        deploy = Path(deploy)
        self.selected: dict[str, list[str]] = {stack: [] for stack, *_ in MODELS}
        config = values(deploy / "secrets/.env")
        keys = set(config) | set(values(deploy / "secrets/dify.env"))
        clean = {
            k: v for k, v in os.environ.items() if k not in keys and not k.startswith("COMPOSE_")
        }
        resolved = {}
        for stack, suffix, filename, envfile in MODELS:
            model = json.loads(
                run(
                    [
                        "docker",
                        "compose",
                        "--env-file",
                        str(deploy / envfile),
                        "-f",
                        str(deploy / filename),
                        "--profile",
                        "*",
                        "config",
                        "--format",
                        "json",
                    ],
                    env=clean,
                )
            )
            if model["name"] != project + suffix:
                raise ValueError("发布项目名不符")
            for volume in model.get("volumes", {}).values():
                if volume.get("external") or not volume["name"].startswith(project + suffix + "_"):
                    raise ValueError("发布数据卷不属于当前项目")
            for network in model.get("networks", {}).values():
                if not (
                    network["name"].startswith(project + suffix + "_")
                    or network["name"] == project + "_edge"
                ):
                    raise ValueError("发布不能接入其它项目网络")
            self.selected[stack] = [n for n in model["services"] if suffix + "/" + n in running]
            if stack == "hr":
                self.selected[stack].append("configurator")
                model["services"]["configurator"]["image"] = model["services"]["backend"]["image"]
            for name in self.selected[stack]:
                service = model["services"][name]
                key = suffix + "/" + ("backend" if name == "configurator" else name)
                image = pinned[key] if pinned else service["image"]
                if not re.fullmatch(
                    r"sha256:[a-f0-9]{64}|[^\s]+@sha256:[a-f0-9]{64}|[^\s]+:v?\d+\.\d+[A-Za-z0-9_.-]*",
                    image,
                ):
                    raise ValueError("发布镜像必须固定版本或摘要")
                if image not in resolved:
                    try:
                        result = run(["docker", "image", "inspect", "--format", "{{.Id}}", image])
                    except Exception:
                        run(["docker", "pull", image])
                        result = run(["docker", "image", "inspect", "--format", "{{.Id}}", image])
                    resolved[image] = result.decode().strip()
                identity = resolved[image]
                if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
                    raise ValueError("镜像身份无效")
                if name in KEEP[suffix] and identity != previous[key]:
                    raise ValueError("数据库和队列镜像升级须单独演练")
                service.update(image=identity, pull_policy="never")
            # configurator 必须使用目标 backend，而不是旧的一次性容器镜像。
            if stack == "hr":
                model["services"]["configurator"]["image"] = model["services"]["backend"]["image"]
            write(deploy / f"data/release-{stack}.json", json.dumps(model))
        super().__init__(deploy, project, run, prefix="release")

    def apply(self, migrate):
        # 先停入口和全部应用层；迁移期间不能再有旧代码写库或跑后台任务。
        self.step = "停止应用"
        for stack, suffix in (("edge", ""), ("hr", "-hr"), ("dify", "-dify")):
            names = [
                n for n in self.selected[stack] if n not in KEEP[suffix] and n != "configurator"
            ]
            if names:
                self.compose(stack, "stop", *names)
        self.step = "数据库启动"
        self.start("hr", ["db", "redis-cache", "redis-queue"])
        self.start("dify", ["db_postgres", "redis", "qdrant"])
        self.step = "Frappe配置"
        self.compose("hr", "run", "--rm", "--no-deps", "configurator")
        self.start("hr", ["backend"])
        self.step = "Frappe迁移"
        if migrate is not None:
            migrate()
            backend = self.container("hr", "backend")
            self.call(["docker", "exec", backend, "bench", "--site", "all", "migrate"])
        self.step = "Frappe启动"
        self.start(
            "hr",
            [
                n
                for n in self.selected["hr"]
                if n not in {"configurator", "policy-sync", "kb-gateway", "frappe-mcp"}
            ],
        )
        self.step = "Dify启动"
        self.start("dify", self.selected["dify"])
        self.step = "接口服务启动"
        self.start(
            "hr",
            [n for n in self.selected["hr"] if n in {"policy-sync", "kb-gateway", "frappe-mcp"}],
        )
        self.step = "企业微信入口"
        self.start("edge", self.selected["edge"])
        self.check()

    def check(self):
        self.step = "健康检查"
        collector = Collector(self.project, run=self.call)
        collector.discover()
        expected = {
            suffix + "/" + name
            for stack, suffix, *_ in MODELS
            for name in self.selected[stack]
            if name not in {"configurator", "init_permissions", "create-site"}
        }
        actual = {
            suffix + "/" + name: item for (suffix, name), item in collector.containers.items()
        }
        if not expected <= actual.keys():
            raise ValueError("发布服务缺失")
        collector.healthy([actual[key]["Id"] for key in expected])
        # HTTP 探针走容器内网；管理员公网白名单仍由外网验收单独验证。
        domain = values(self.deploy / "secrets/.env")["EB_DOMAIN"]
        probes = [
            ("dify", "api", "python", "http://web:3000/signin", "dify." + domain),
            (
                "hr",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "http://frontend:8080/login",
                "hr." + domain,
            ),
        ]
        probes += [
            ("hr", name, "python", "http://127.0.0.1:8080/healthz", name + ":8080")
            for name in ("policy-sync", "kb-gateway", "frappe-mcp")
            if name in self.selected["hr"]
        ]
        for stack, name, python, url, host in probes:
            status = (
                self.call(
                    ["docker", "exec", self.container(stack, name), python, "-c", HTTP, url, host]
                )
                .decode()
                .strip()
            )
            if not re.fullmatch(r"2\d\d", status):
                raise ValueError("HTTP检查未通过")
            print(f"发布:HTTP:{name}:{status}")


def running(deploy, run=command):
    config = values(Path(deploy) / "secrets/.env")
    collector = Collector(config["EB_PROJECT"], run=run)
    collector.discover()
    selected = {
        suffix + "/" + name
        for (suffix, name), item in collector.containers.items()
        if item["State"]["Running"]
        and name not in {"configurator", "create-site", "init_permissions"}
    }
    previous = {key: record["image_id"] for key, record in collector.images.items()}
    return collector, selected, previous


def prepare(deploy, run=command):
    deploy = Path(deploy)
    run(["make", "-C", str(deploy), "prepare"])
    run([sys.executable, str(deploy / "prepare.py"), "check", str(deploy / "secrets/.env")])
