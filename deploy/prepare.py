"""仅组装官方部署文件；生成物在 data/，密钥在 secrets/。"""
import base64
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
os.umask(0o077)
LOCK = json.loads(Path("upstream.lock.json").read_text())


def env(path):
    return dict(line.split("=", 1) for line in Path(path).read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def write(path, data):
    Path(path).write_text(data)
    Path(path).chmod(0o600)


def source(name):
    item, dest = LOCK["sources"][name], ROOT / "data" / name
    if not dest.exists():
        dest.mkdir(parents=True)
        for args in (["init"], ["remote", "add", "origin", item["url"]],
                     ["config", "core.sparseCheckout", "true"]):
            subprocess.run(["git", "-C", str(dest), *args], check=True, stdout=subprocess.DEVNULL)
        (dest / ".git/info/sparse-checkout").write_text(item["paths"])
    current = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True)
    if current.stdout.strip() != item["commit"]:
        subprocess.run(["git", "-C", str(dest), "fetch", "--depth", "1", "--filter=blob:none", "origin", item["commit"]], check=True)
        subprocess.run(["git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD"], check=True)
    return dest


mode = sys.argv[1]
if mode == "init":
    Path("secrets").mkdir(exist_ok=True, mode=0o700)
    if not Path("secrets/.env").exists():
        write("secrets/.env", Path(".env.example").read_text().replace("CHANGE_ME", "__GENERATE__"))
        values = env("secrets/.env")
        for key, value in values.items():
            if value == "__GENERATE__":
                values[key] = secrets.token_urlsafe(32) if key in {"DIFY_AGENT_SERVER_SECRET_KEY", "EB_SYNC_WEBHOOK_SECRET", "EB_SYNC_SERVICE_TOKEN"} else secrets.token_hex(32)
        write("secrets/.env", "".join(f"{k}={v}\n" for k, v in values.items()))
    else:
        values = env("secrets/.env")
        with Path("secrets/.env").open("a") as target:
            for key in ("EB_SYNC_WEBHOOK_SECRET", "EB_SYNC_SERVICE_TOKEN"):
                if key not in values:
                    target.write(f"\n{key}={secrets.token_urlsafe(32)}\n")
    print("已初始化 secrets/.env（未打印密钥）；再次运行不会覆盖。")
    sys.exit()
settings = env(sys.argv[2] if mode in {"check", "wechat", "policy-sync"} else mode)
try:
    admin_ranges = [ipaddress.ip_network(value) for value in settings.get("EB_ADMIN_ALLOW_CIDRS", "127.0.0.1/32").split()]
    if not admin_ranges or any(network.prefixlen == 0 for network in admin_ranges):
        raise ValueError
except ValueError:
    sys.exit("管理后台来源必须是有效 IP/CIDR；不允许空白、全网范围或配置表达式。")
required = [k for k, v in env(".env.example").items() if v == "CHANGE_ME"]
if mode != "wechat":
    agent_key = settings.get("DIFY_AGENT_SERVER_SECRET_KEY", "")
    if (not re.fullmatch(r"[A-Za-z0-9_-]{43}", agent_key)
            or base64.urlsafe_b64encode(base64.urlsafe_b64decode(agent_key + "=")).decode().rstrip("=") != agent_key):
        sys.exit("DIFY_AGENT_SERVER_SECRET_KEY 必须是 32 字节无填充 base64url；旧测试环境修复步骤见 LOCAL.md。")
if mode in {"check", "wechat", "policy-sync"}:
    keys = required if mode == "check" else ([k for k in env(".env.example") if k.startswith("WECHAT") or k == "DIFY_API_KEY"] if mode == "wechat" else ["EB_SYNC_WEBHOOK_SECRET", "EB_SYNC_SERVICE_TOKEN", "EB_SYNC_FRAPPE_API_KEY", "EB_SYNC_FRAPPE_API_SECRET", "EB_SYNC_DIFY_DATASET_API_KEY"])
    if any(not settings.get(k) or settings[k] == "CHANGE_ME" for k in keys):
        sys.exit("必填凭据未配置；请只在服务器编辑 secrets/.env。")
    if mode == "policy-sync" and not re.fullmatch(r"ghcr.io/qianjie0720-sys/enterprise-base-policy-sync@sha256:[a-f0-9]{64}", settings.get("EB_POLICY_SYNC_IMAGE", "")):
        sys.exit("policy-sync 必须使用本仓库 CI 发布的 SHA256 镜像摘要。")
    if mode == "policy-sync" and any(len(settings[k]) < 32 for k in ("EB_SYNC_WEBHOOK_SECRET", "EB_SYNC_SERVICE_TOKEN")):
        sys.exit("两项 X5 密钥都必须至少 32 位。")
    sys.exit()
if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", settings["EB_PROJECT"]) or not re.fullmatch(r"[a-zA-Z0-9.-]+", settings["EB_DOMAIN"]):
    sys.exit("项目名或域名格式错误。")
if settings["EB_SCHEME"] not in {"http", "https"}:
    sys.exit("EB_SCHEME 仅允许 http 或 https。")
Path("secrets").mkdir(exist_ok=True, mode=0o700)
Path("data").mkdir(exist_ok=True)
caddyfile = Path("Caddyfile").read_text()
# 仅精确绑定 IPv4 回环时由宿主机端口隔离；其它绑定保持来源限制。
if (settings.get("EB_BIND") or "127.0.0.1") == "127.0.0.1":
    caddyfile = caddyfile.replace("        import admin_only\n", "")
write("data/Caddyfile", caddyfile)
project = settings["EB_PROJECT"]
edge = {"external": True, "name": f"{project}_edge"}
dify, frappe = source("dify") / "docker", source("frappe")
values = env(dify / ".env.example")
values.update({k: v for k, v in settings.items() if k in required and not k.startswith("EB_")})
base = f'{settings["EB_SCHEME"]}://dify.{settings["EB_DOMAIN"]}'
values.update(VECTOR_STORE="qdrant", COMPOSE_PROFILES="qdrant,postgresql,collaboration",
              QDRANT_URL="http://qdrant:6333", QDRANT_GRPC_ENABLED="false",
              CELERY_BROKER_URL=f'redis://:{settings["REDIS_PASSWORD"]}@redis:6379/1',
              CODE_EXECUTION_API_KEY=settings["SANDBOX_API_KEY"], CONSOLE_API_URL=base,
              CONSOLE_WEB_URL=base, SERVICE_API_URL=base, APP_API_URL=base, APP_WEB_URL=base,
              FILES_URL=base, TRIGGER_URL=base, ENDPOINT_URL_TEMPLATE=base+"/e/{hook_id}",
              NEXT_PUBLIC_SOCKET_URL=base.replace("http", "ws", 1), CHECK_UPDATE_URL="",
              NEXT_TELEMETRY_DISABLED="1", ENABLE_REQUEST_LOGGING="false", LOG_LEVEL="WARNING",
              API_SENTRY_DSN="", WEB_SENTRY_DSN="", AMPLITUDE_API_KEY="", PLUGIN_SENTRY_ENABLED="false")
write("secrets/dify.env", "".join(f"{k}={v}\n" for k, v in values.items()))
doc = yaml.safe_load((dify / "docker-compose.yaml").read_text())
active = {k: v for k, v in doc["services"].items() if not v.get("profiles") or set(v["profiles"]) & {"qdrant", "postgresql", "collaboration"}}
doc = {"name": f"{project}-dify", "services": active, "networks": doc["networks"], "volumes": doc["volumes"]}
for name, svc in active.items():
    svc.pop("profiles", None)
    svc.pop("ports", None)
    if name in LOCK["dify_images"]:
        svc["image"] = LOCK["dify_images"][name]
    deps = svc.get("depends_on", {})
    svc["depends_on"] = ({k: v for k, v in deps.items() if k in active} if isinstance(deps, dict)
                         else [k for k in deps if k in active])
    svc["env_file"] = [str(ROOT / "secrets/dify.env")] if svc.get("env_file") else []
    if name == "plugin_daemon":
        svc.setdefault("environment", {})["LOG_LEVEL"] = "WARN"
    mounts = []
    for mount in svc.get("volumes", []):
        src, target, *options = mount.split(":")
        if src == "./volumes/sandbox/conf":
            options = ["ro"]
        elif src.startswith("./volumes/"):
            src = "data_" + re.sub(r"[^a-zA-Z0-9]", "_", src[10:])
            doc["volumes"][src] = {}
        mounts.append(":".join([src, target, *options]))
    svc["volumes"] = mounts
doc["networks"]["edge"] = edge
active["nginx"]["networks"] = {"default": {}, "edge": {"aliases": ["dify"]}}
write(dify / "eb-compose.yaml", yaml.safe_dump(doc, sort_keys=False))
hr = yaml.safe_load((frappe / "compose.yaml").read_text())
hr = {k: v for k, v in hr.items() if not k.startswith("x-")}
hr["name"] = f"{project}-hr"
for svc in hr["services"].values():
    svc["image"], svc["pull_policy"] = LOCK["frappe_image"], "never"
hr["services"].update(yaml.safe_load(Path("frappe-services.yaml").read_text())["services"])
hr["networks"] = {"default": {}, "edge": edge}
hr["volumes"].update({"db": {}, "cache": {}, "queue": {}, "logs": {}})
for svc in hr["services"].values():
    if svc.get("image") == LOCK["frappe_image"]:
        svc.setdefault("volumes", []).append("logs:/home/frappe/frappe-bench/logs")
for name in ("backend", "create-site"):
    hr["services"][name]["env_file"] = [str(ROOT / "secrets/frappe-integration.env")]
hr["services"]["policy-sync"]["env_file"] = [str(ROOT / "secrets/policy-sync.env")]
sync = env(ROOT.parent / "services/policy_sync/.env.example")
sync.update(EB_SYNC_WEBHOOK_SECRET=settings.get("EB_SYNC_WEBHOOK_SECRET", ""),
            EB_SYNC_SERVICE_TOKEN=settings.get("EB_SYNC_SERVICE_TOKEN", ""),
            EB_FRAPPE_URL="http://frontend:8080", EB_DIFY_API_BASE="http://dify/v1",
            EB_FRAPPE_API_KEY=settings.get("EB_SYNC_FRAPPE_API_KEY", ""),
            EB_FRAPPE_API_SECRET=settings.get("EB_SYNC_FRAPPE_API_SECRET", ""),
            EB_DIFY_DATASET_API_KEY=settings.get("EB_SYNC_DIFY_DATASET_API_KEY", ""))
write("secrets/policy-sync.env", "".join(f"{k}={v}\n" for k, v in sync.items()))
write("secrets/frappe-integration.env", f'EB_SYNC_WEBHOOK_SECRET={settings.get("EB_SYNC_WEBHOOK_SECRET", "")}\n')
cfg = hr["services"]["configurator"]
cfg["environment"].update(DB_HOST="db", DB_PORT="3306", REDIS_CACHE="redis-cache:6379", REDIS_QUEUE="redis-queue:6379")
cfg["depends_on"] = {"db": {"condition": "service_healthy"}, "redis-cache": {"condition": "service_healthy"}, "redis-queue": {"condition": "service_healthy"}}
hr["services"]["frontend"]["environment"]["FRAPPE_SITE_NAME_HEADER"] = "hr.${EB_DOMAIN}"
hr["services"]["frontend"]["networks"] = {"default": {}, "edge": {"aliases": ["frappe"]}}
write(frappe / "eb-compose.yaml", yaml.safe_dump(hr, sort_keys=False))
wechat = {k: v for k, v in settings.items() if k.startswith("WECHAT") or k == "DIFY_API_KEY"}
write("secrets/wechat.env", "".join(f"{k}={v}\n" for k, v in wechat.items()))
write("data/plugins.json", json.dumps({"plugins": {name: {"enabled": False, "priority": 0} for name in LOCK["wechat_plugins"]}}))
print("官方部署定义已生成；未启动容器、未打印密钥。")
