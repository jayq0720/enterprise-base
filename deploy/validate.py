"""隔离验证部署配置与入口限制；不构建镜像或启动业务容器。"""
import copy
import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from caddy_check import verify_access
from wecom_cards import configuration

ROOT = Path(__file__).resolve().parent
COMPOSE = shlex.split(os.environ.get("EB_COMPOSE_COMMAND", "docker compose"))
FILES = ("prepare.py", ".env.example", "upstream.lock.json", "apps.lock.json", "caddy.lock.json",
         "docker-compose.yml", "frappe-services.yaml", "Caddyfile", "wecom_cards.py")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command, cwd, success=True, include_stderr=False):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    require((result.returncode == 0) == success,
            "子命令返回码不符；输出已隐藏，防止泄露测试密钥。")
    return result.stdout + result.stderr if include_stderr or not success else result.stdout


def settings(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def audit(model, stack):
    if stack == "dify":
        services = model["services"]
        require(services["nginx"]["image"] == "nginx:1.28.0@sha256:552e7481ca93ffccd046aa658dbbed22caefbc09c66fa7cd247cbb90b8a5c609",
                "Dify nginx 必须使用已核验摘要的 Debian 版，提供 bash。")
        require(services["plugin_daemon"]["environment"]["LOG_LEVEL"] == "WARN"
                and all(services[name]["environment"]["LOG_LEVEL"] == "WARNING"
                        for name in ("api", "worker", "worker_beat")), "插件/Python 服务日志级别不兼容。")
        key = services["agent_backend"]["environment"]["DIFY_AGENT_SERVER_SECRET_KEY"]
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", key))
                and len(base64.urlsafe_b64decode(key + "=")) == 32, "Agent 密钥不是 32 字节无填充 base64url。")
        require({"default", "ssrf_proxy_network", "edge"} == set(services["ssrf_proxy"]["networks"]),
                "Dify 代理无法在内网访问 X3/X4。")
        require(all("edge" not in services[n]["networks"] for n in ("api", "worker")),
                "Dify api/worker 不得直连 edge，须经 SSRF 代理访问 X3/X4。")
        proxy = services["ssrf_proxy"]["environment"]
        require(proxy["SSRF_PROXY_ALLOW_PRIVATE_DOMAINS"] == "kb-gateway,frappe-mcp"
                and not proxy["SSRF_PROXY_ALLOW_PRIVATE_IPS"]
                and not services["agent_ssrf_proxy"]["environment"]["SSRF_PROXY_ALLOW_PRIVATE_DOMAINS"],
                "SSRF 放行范围必须仅限 X3/X4，且不扩大 Agent 代理权限。")
    if stack == "frappe":
        for name in ("kb-gateway", "frappe-mcp"):
            service = model["services"][name]
            require(bool(re.fullmatch(r"ghcr.io/qianjie0720-sys/enterprise-base-" + name + r"@sha256:[a-f0-9]{64}", service["image"]))
                    and set(service["networks"]) == {"default", "edge"}
                    and not service.get("network_mode") and not service.get("privileged"), "X3/X4 镜像或网络隔离错误。")
            require(service["healthcheck"]["test"] == ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"],
                    "X3/X4 健康检查必须 GET /healthz。")
    for service in model["services"].values():
        image = service["image"]
        # PostgreSQL 10+ 使用主版本.补丁版本，其它当前组件使用三段版本。
        version = r"\d+\.\d+" if image.split(":")[0].split("/")[-1] == "postgres" else r"v?\d+\.\d+\.\d+"
        require(bool(re.fullmatch(r".+@sha256:[a-f0-9]{64}", image)
                     or re.fullmatch(r"[^@]+:" + version + r"[-.a-zA-Z0-9]*", image)),
                f"{stack} 存在未固定版本的镜像。")
        require(not service.get("ports") or (stack == "edge" and service is model["services"]["caddy"]),
                f"{stack} 存在非 Caddy 的公开端口。")
        for mount in service.get("volumes", []):
            if mount["type"] == "volume":
                require(bool(mount.get("source")) and mount["source"] in model["volumes"],
                        f"{stack} 存在匿名数据卷。")
            if mount["type"] == "bind" and "/volumes/" in mount["source"]:
                require(mount["target"] == "/conf" and mount.get("read_only"),
                        f"{stack} 数据仍写入上游绑定目录。")


def reject_mutations(model):
    for defect in ("image", "ports", "volume", "nginx", "plugin_log", "worker_log", "agent_key", "proxy", "network", "direct"):
        bad = copy.deepcopy(model)
        api = bad["services"]["api"]
        if defect == "image":
            api["image"] = "langgenius/dify-api:latest"
        elif defect == "ports":
            api["ports"] = [{"target": 5001, "published": "5001"}]
        elif defect == "volume":
            api["volumes"] = [{"type": "volume", "target": "/app/api/storage"}]
        elif defect == "nginx":
            bad["services"]["nginx"]["image"] = "nginx:1.28.0-alpine"
        elif defect == "plugin_log":
            bad["services"]["plugin_daemon"]["environment"]["LOG_LEVEL"] = "WARNING"
        elif defect == "worker_log":
            bad["services"]["worker"]["environment"]["LOG_LEVEL"] = "WARN"
        elif defect == "agent_key":
            bad["services"]["agent_backend"]["environment"]["DIFY_AGENT_SERVER_SECRET_KEY"] = "a" * 64
        elif defect == "proxy":
            bad["services"]["ssrf_proxy"]["environment"]["SSRF_PROXY_ALLOW_PRIVATE_IPS"] = "10.0.0.0/8"
        elif defect == "network":
            bad["services"]["ssrf_proxy"]["networks"].pop("edge")
        else:
            api["networks"]["edge"] = {}
        try:
            audit(bad, "dify")
        except RuntimeError:
            continue
        raise RuntimeError(f"反例未被拒绝：{defect}。")


def check_bootstrap(service, work):
    # 执行真实 shell 命令、替换 bench 为参数记录器；不模拟数据库验收。
    folder = work / "bootstrap-probe"
    folder.mkdir(exist_ok=True)
    trace = folder / "trace.jsonl"
    bench = folder / "bench"
    bench.write_text(f"#!{sys.executable}\n" + """import json, os, sys
with open(os.environ['EB_BOOTSTRAP_TRACE'], 'a') as trace:
    trace.write(json.dumps(sys.argv[1:]) + '\\n')
if 'execute' in sys.argv and os.environ.get('EB_FAIL_TELEMETRY') == '1':
    sys.exit(1)
""")
    bench.chmod(0o700)
    environment = {**os.environ, "PATH": f"{folder}{os.pathsep}{os.environ['PATH']}",
                   "EB_BOOTSTRAP_TRACE": str(trace), "SITE": "test.invalid", "SITE_URL": "http://test.invalid",
                   "DB_ROOT_PASSWORD": "synthetic-test-value", "ADMIN_PASSWORD": "synthetic-test-value"}
    for failed in (False, True):
        trace.write_text("")
        # Compose config 输出为可再次载入的配置，需还原 $$ 转义才等同容器命令。
        command = [part.replace("$$", "$") for part in service["command"]]
        result = subprocess.run([*service["entrypoint"], *command], cwd=folder,
                                env={**environment, "EB_FAIL_TELEMETRY": str(int(failed))}, capture_output=True)
        require((result.returncode != 0) == failed, "建站 shell 未按失败停止。")
        calls = [json.loads(line) for line in trace.read_text().splitlines()]
        execute = [args for args in calls if "execute" in args]
        require(len(execute) == 1 and execute[0][3:5] == ["frappe.db.set_single_value", "--args"]
                and json.loads(execute[0][5]) == ["System Settings", "enable_telemetry", 0],
                "遥测必须使用数据库 Single 接口，参数须保持整数 0。")
        require((calls[-1] == ["--site", "test.invalid", "enable-scheduler"]) != failed,
                "调度器必须在成功设置遥测后开启。")


def reuse_source_cache(work, lock):
    # 仅复用固定提交的 Git 对象；不复制服务器 data/ 或 secrets/ 中的运行文件。
    for name, item in lock["sources"].items():
        cached, target = ROOT / "data" / name, work / "data" / name
        if not (cached / ".git").exists():
            continue
        head = run(["git", "rev-parse", "HEAD"], cached).strip()
        if head != item["commit"]:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--shared", "--no-checkout", str(cached), str(target)], work)
        run(["git", "config", "core.sparseCheckout", "true"], target)
        (target / ".git/info/sparse-checkout").write_text(item["paths"])
        run(["git", "checkout", "--detach", item["commit"]], target)


def validate(work):
    def prepare(*args, success=True):
        return run([sys.executable, "prepare.py", *args], work, success, include_stderr=True)

    output = prepare("init")
    private = work / "secrets/.env"
    before = private.read_bytes()
    output += prepare("init")
    require(private.read_bytes() == before, "重复初始化覆盖了密钥。")
    values = settings(private)
    original = dict(values)
    upgraded = {"EB_SYNC_WEBHOOK_SECRET", "EB_SYNC_SERVICE_TOKEN", "EB_KB_SERVICE_TOKEN", "EB_MCP_SERVICE_TOKEN", "EB_MCP_SESSION_SECRET"}
    added = {k for k in values if k.startswith(("EB_KB_", "EB_MCP_", "EB_FRAPPE_MCP_"))}
    private.write_text("".join(f"{k}={v}\n" for k, v in values.items()
                               if k not in upgraded | added))
    prepare("init")
    values = settings(private)
    require(all(values[k] == v for k, v in original.items() if k not in upgraded),
            "补齐服务密钥时覆盖了已有配置。")
    # 旧服务器上的镜像摘要过期时，重复 init 要改成当前代码的版本，且不动密钥。
    stale = {**values, "EB_KB_GATEWAY_IMAGE": "ghcr.io/qianjie0720-sys/enterprise-base-kb-gateway@sha256:" + "0" * 64}
    private.write_text("".join(f"{k}={v}\n" for k, v in stale.items()))
    prepare("init")
    require(settings(private) == {**values, "EB_KB_GATEWAY_IMAGE": settings(work / ".env.example")["EB_KB_GATEWAY_IMAGE"]},
            "重复初始化未更新镜像摘要，或改动了其它配置。")
    values = settings(private)
    secret_keys = [k for k, v in settings(work / ".env.example").items() if v == "CHANGE_ME"]
    require(len({values[k] for k in secret_keys}) == len(secret_keys), "服务未使用独立随机密钥。")
    for key in ("EB_SYNC_WEBHOOK_SECRET", "EB_SYNC_SERVICE_TOKEN"):
        require(len(base64.urlsafe_b64decode(values[key] + "=")) == 32 and "=" not in values[key],
                "X5 密钥必须是 32 字节无填充 base64url。")
    prepare("check", "secrets/.env")
    prepare("check", ".env.example", success=False)
    malformed = work / "secrets/malformed.env"
    for key in ("a" * 64, values["DIFY_AGENT_SERVER_SECRET_KEY"] + "=", "invalid"):
        malformed.write_text("".join(f"{k}={key if k == 'DIFY_AGENT_SERVER_SECRET_KEY' else v}\n" for k, v in values.items()))
        prepare("check", str(malformed), success=False)
        prepare(str(malformed), success=False)
    malformed.unlink()
    prepare("wechat", "secrets/.env", success=False)
    prepare("policy-sync", "secrets/.env", success=False)
    prepare("hr-assistant", "secrets/.env", success=False)
    # 缺失整个配置项也必须拒绝，不能只检查已有键。
    incomplete = work / "secrets/incomplete.env"
    assistant = dict(values)
    credentials = ("EB_KB_FRAPPE_API_KEY", "EB_KB_FRAPPE_API_SECRET", "EB_KB_DIFY_DATASET_API_KEY", "EB_MCP_FRAPPE_API_KEY", "EB_MCP_FRAPPE_API_SECRET")
    assistant.update({key: "synthetic-" + key for key in credentials})
    incomplete.write_text("".join(f"{k}={v}\n" for k, v in assistant.items()))
    prepare("hr-assistant", str(incomplete))
    for defect, replacement in {**dict.fromkeys(credentials, ""), "EB_KB_GATEWAY_IMAGE": "image:latest",
            "EB_FRAPPE_MCP_IMAGE": "image:latest", "EB_MCP_SESSION_SECRET": values["EB_MCP_SERVICE_TOKEN"],
            "EB_KB_SERVICE_TOKEN": "short", "EB_MCP_FRAPPE_API_KEY": assistant["EB_KB_FRAPPE_API_KEY"]}.items():
        incomplete.write_text("".join(f"{k}={replacement if k == defect else v}\n" for k, v in assistant.items()))
        prepare("hr-assistant", str(incomplete), success=False)
    values.update({key: assistant[key] for key in credentials})
    policy = dict(values)
    api_keys = ("EB_SYNC_FRAPPE_API_KEY", "EB_SYNC_FRAPPE_API_SECRET", "EB_SYNC_DIFY_DATASET_API_KEY")
    policy.update({key: "synthetic-test-value" for key in api_keys})
    incomplete.write_text("".join(f"{k}={v}\n" for k, v in policy.items()))
    prepare("policy-sync", str(incomplete))
    for missing in api_keys:
        incomplete.write_text("".join(f"{k}={v}\n" for k, v in policy.items() if k != missing))
        prepare("policy-sync", str(incomplete), success=False)
    incomplete.write_text("\n".join(f"{k}={'synthetic-test-value' if k.startswith('WECHAT') or k == 'DIFY_API_KEY' else v}"
                                     for k, v in values.items()) + "\n")
    prepare("wechat", str(incomplete))
    incomplete.write_text("\n".join(line for line in incomplete.read_text().splitlines()
                                     if not line.startswith("WECHATCOMAPP_TOKEN=")) + "\n")
    prepare("wechat", str(incomplete), success=False)
    for cidrs in ("", "not-a-cidr", "0.0.0.0/0", "::/0"):
        incomplete.write_text("".join(f"{k}={cidrs if k == 'EB_ADMIN_ALLOW_CIDRS' else v}\n" for k, v in values.items()))
        prepare("check", str(incomplete), success=False)
    incomplete.write_text("".join(f"{k}={v}\n" for k, v in values.items() if k != "EB_ADMIN_ALLOW_CIDRS"))
    prepare("check", str(incomplete))
    incomplete.unlink()
    require(configuration(values) == {"enabled": False}, "旧配置未默认关闭审批卡片。")
    cards = {**values, "EB_WECOM_CALLBACK_MODE": "approvals", "EB_SCHEME": "https",
             "WECHATCOM_CORP_ID": "synthetic", "WECHATCOMAPP_AGENT_ID": "1001",
             "WECHATCOMAPP_SECRET": "synthetic-secret", "WECHATCOMAPP_TOKEN": "synthetic-token", "DIFY_API_KEY": "synthetic-key",
             "WECHATCOMAPP_AES_KEY": base64.b64encode(bytes(range(32))).decode().rstrip("=")}
    require(configuration(cards)["enabled"] is True, "合法卡片配置未通过。")
    for key, value in (("WECHATCOMAPP_SECRET", ""), ("WECHATCOMAPP_AES_KEY", "bad"), ("WECHATCOMAPP_AGENT_ID", "bad")):
        try:
            configuration({**cards, key: value})
        except ValueError:
            continue
        raise RuntimeError("无效卡片配置未被拒绝。")
    incomplete.write_text("".join(f"{k}={v}\n" for k, v in cards.items()))
    incomplete.chmod(0o600)
    run([sys.executable, "wecom_cards.py", str(incomplete), "--check"], work)
    incomplete.chmod(0o644)
    run([sys.executable, "wecom_cards.py", str(incomplete), "--check"], work, success=False)
    incomplete.chmod(0o600)
    for invalid in ("other", "approvals"):
        incomplete.write_text("".join(f"{k}={v}\n" for k, v in {**values, "EB_WECOM_CALLBACK_MODE": invalid}.items()))
        prepare("check", str(incomplete), success=False)
    incomplete.unlink()
    stacks = (("edge", "secrets/.env", "docker-compose.yml", "wechat"),
              ("dify", "secrets/dify.env", "data/dify/docker/eb-compose.yaml", None),
              ("frappe", "secrets/.env", "data/frappe/eb-compose.yaml", "bootstrap"))
    for project, domain, scheme, bind, cidrs in (("eb", "localhost", "http", "127.0.0.1", "127.0.0.1/32"),
            ("eb-ci-review", "example.invalid", "https", "0.0.0.0", "203.0.113.0/24 2001:db8::/64"),
            ("eb-local-recheck", "localhost", "http", "127.0.0.1", "203.0.113.0/24"),
            ("eb-public-allow", "example.invalid", "https", "0.0.0.0", "127.0.0.1/32")):
        values.update(EB_PROJECT=project, EB_DOMAIN=domain, EB_SCHEME=scheme, EB_BIND=bind, EB_ADMIN_ALLOW_CIDRS=cidrs)
        callback_mode = "approvals" if scheme == "https" else "bridge"
        values["EB_WECOM_CALLBACK_MODE"] = callback_mode
        private.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        output += prepare("secrets/.env")
        for stack, environment, file, profile in stacks:
            command = [*COMPOSE, "--env-file", environment, "-f", file]
            if profile:
                command += ["--profile", profile]
            if stack == "frappe":
                command += ["--profile", "policy-sync", "--profile", "hr-assistant"]
            run([*command, "config", "--quiet"], work)
            rendered = run([*command, "config", "--format", "json"], work)
            model = json.loads(rendered)
            audit(model, stack)
            require(model["name"] == project + {"edge": "", "dify": "-dify", "frappe": "-hr"}[stack],
                    "项目名未正确传递。")
            services = model["services"]
            if stack == "edge":
                caddy, wechat = services["caddy"], services["wechat"]
                require(set(wechat["networks"]) == {"edge", "wecom-callback"} and set(caddy["networks"]) == {"edge"}
                        and model["networks"]["wecom-callback"]["internal"], "聊天桥接专用网络或入口隔离错误。")
                require(caddy["environment"]["DIFY_ADDRESS"] == f"{scheme}://dify.{domain}",
                        "域名/协议未正确传递到入口。")
                require(all(p["host_ip"] == bind for p in caddy["ports"]), "入口绑定地址未正确传递。")
                require(caddy["environment"]["EB_ADMIN_ALLOW_CIDRS"] == cidrs, "管理员白名单未正确传递。")
                require(any(Path(m["source"]).resolve() == (work / "data/Caddyfile").resolve() and m["target"] == "/etc/caddy/Caddyfile"
                            and m.get("read_only") for m in caddy["volumes"]), "入口未挂载生成的 Caddy 配置。")
                verify_access(work, caddy["environment"], bind, callback_mode=callback_mode)
                generated = (work / "data/Caddyfile").read_text()
                incorrect = ((work / "Caddyfile").read_text() if bind == "127.0.0.1"
                             else generated.replace("import admin_only", ""))
                try:
                    verify_access(work, caddy["environment"], bind, incorrect, run_probe=False, callback_mode=callback_mode)
                except RuntimeError:
                    pass
                else:
                    raise RuntimeError("本机误设限制或对外遗漏限制的反例未被拒绝。")
                print(f"{project}/caddy：本机免限制/对外白名单、伪造头、回调边界及错误配置反例通过。")
                require(wechat["logging"]["driver"] == "none" and ">/dev/null 2>&1" in wechat["entrypoint"][-1],
                        "企业微信输出未关闭。")
                require(any(m["source"] == "/dev/null" and m["target"] == "/app/run.log"
                            for m in wechat["volumes"]), "企业微信文件日志未丢弃。")
            elif stack == "dify":
                require("qdrant" in services and "weaviate" not in services, "向量库不是 Qdrant。")
                api = services["api"]["environment"]
                require(api["VECTOR_STORE"] == "qdrant" and api["CODE_EXECUTION_API_KEY"] == values["SANDBOX_API_KEY"],
                        "Dify 向量配置或 Sandbox 密钥不一致。")
                require(services["agent_backend"]["environment"]["DIFY_AGENT_SERVER_SECRET_KEY"]
                        == values["DIFY_AGENT_SERVER_SECRET_KEY"], "Agent 密钥未正确传递。")
                require(services["sandbox"]["environment"]["API_KEY"] == values["SANDBOX_API_KEY"], "Sandbox 密钥未传递。")
                require(set(services["sandbox"]["networks"]) == {"ssrf_proxy_network"}
                        and model["networks"]["ssrf_proxy_network"]["internal"], "Sandbox 隔离网络被放宽。")
                reject_mutations(model)
            else:
                if callback_mode == "approvals":
                    require(set(services["backend"]["networks"]) == {"default", "wecom-callback"}
                            and model["networks"]["wecom-callback"]["external"]
                            and model["networks"]["wecom-callback"]["name"] == project + "_wecom_callbacks",
                            "后端无法经专用网络桥接聊天，或网络边界错误。")
                require((services["frontend"].get("logging", {}).get("driver") == "none") == (callback_mode == "approvals"),
                        "审批回调未禁止 Nginx 完整查询日志。")
                require(services["frontend"]["environment"]["FRAPPE_SITE_NAME_HEADER"] == f"hr.{domain}",
                        "Frappe 站点域名未传递。")
                require("edge" not in services["backend"]["networks"], "Frappe 后端直接接入入口网络。")
                sync = services["policy-sync"]
                require(not sync.get("ports") and set(sync["networks"]) == {"default", "edge"},
                        "X5 端口或网络边界错误。")
                require(sync["environment"]["EB_FRAPPE_URL"] == "http://frontend:8080"
                        and sync["environment"]["EB_DIFY_API_BASE"] == "http://dify/v1"
                        and sync["environment"]["EB_SYNC_WEBHOOK_SECRET"] == values["EB_SYNC_WEBHOOK_SECRET"],
                        "X5 内网地址或签名密钥未正确传递。")
                require(services["create-site"]["environment"]["EB_SYNC_WEBHOOK_SECRET"]
                        == values["EB_SYNC_WEBHOOK_SECRET"], "Frappe 未得到签名密钥。")
                check_bootstrap(services["create-site"], work)
                for name, prefix in (("kb-gateway", "EB_KB"), ("frappe-mcp", "EB_MCP")):
                    config = services[name]["environment"]
                    require(config["EB_FRAPPE_URL"] == "http://frontend:8080"
                            and all(config["EB_FRAPPE_API_" + suffix] == values[prefix + "_FRAPPE_API_" + suffix] for suffix in ("KEY", "SECRET"))
                            and config[prefix + "_SERVICE_TOKEN"] == values[prefix + "_SERVICE_TOKEN"], "X3/X4 地址、专用凭据映射错误。")
                    require(set(config) == set(settings(ROOT.parent / f"services/{name.replace('-', '_')}/.env.example")), "X3/X4 收到了额外配置或其它服务密钥。")
                    for defect in ("ports", "networks", "image"):
                        bad = copy.deepcopy(model)
                        bad["services"][name][defect] = {"ports": [{"target": 8080, "published": "8080"}], "networks": {"default": {}}, "image": "image:latest"}[defect]
                        try:
                            audit(bad, "frappe")
                        except RuntimeError:
                            continue
                        raise RuntimeError("X3/X4 隔离反例未被拒绝。")
                require(services["kb-gateway"]["environment"]["EB_DIFY_API_BASE"] == "http://dify/v1"
                        and services["kb-gateway"]["environment"]["EB_DIFY_DATASET_API_KEY"] == values["EB_KB_DIFY_DATASET_API_KEY"]
                        and services["frappe-mcp"]["environment"]["EB_MCP_SESSION_SECRET"] == values["EB_MCP_SESSION_SECRET"]
                        and json.loads(services["frappe-mcp"]["environment"]["EB_MCP_ALLOWED_HOSTS"]) == ["frappe-mcp:8080"], "X3/X4 检索、会话签名或 MCP Host 配置错误。")
            print(f"{project}/{stack}：配置通过，镜像固定、数据卷命名、端口边界正确。")
    print("四类启动兼容性反例被拒绝；建站参数与失败停止检查通过。")
    require(all(values[k] not in output for k in secret_keys), "初始化或生成输出包含密钥。")
    require(all(f.stat().st_mode & 0o777 == 0o600 for f in (work / "secrets").iterdir()), "密钥文件权限不是 600。")
    require((work / "secrets").stat().st_mode & 0o777 == 0o700, "密钥目录权限不是 700。")
    print("初始化幂等、密钥权限与缺项拒绝通过；无效白名单、三类配置反例均被拒绝。")
    print("X3/X4：独立凭据、补齐配置、MCP Host、健康检查、隔离与 Git 忽略通过；SSRF 仅放行两项服务名。")


def main():
    require(shutil.which(COMPOSE[0]) is not None, "找不到 Compose 校验程序。")
    for name in ("kb-gateway", "frappe-mcp"):
        run(["git", "check-ignore", "-q", f"secrets/{name}.env"], ROOT)
    with tempfile.TemporaryDirectory(prefix="eb-validation-") as temp:
        work = Path(temp) / "deploy"
        work.mkdir()
        for name in FILES:
            shutil.copyfile(ROOT / name, work / name)
        config = Path(temp) / "config/dify-on-wechat"
        config.mkdir(parents=True)
        shutil.copyfile(ROOT.parent / "config/dify-on-wechat/config.json", config / "config.json")
        for name in ("policy_sync", "kb_gateway", "frappe_mcp"):
            folder = Path(temp) / "services" / name
            folder.mkdir(parents=True)
            shutil.copyfile(ROOT.parent / f"services/{name}/.env.example", folder / ".env.example")
        reuse_source_cache(work, json.loads((work / "upstream.lock.json").read_text()))
        validate(work)
    print("隔离校验结束；未启动容器，未读写现有部署密钥或运行数据。")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"部署校验失败：{error}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, IndexError) as error:
        print(f"部署校验失败：{type(error).__name__}；原始输出已隐藏。", file=sys.stderr)
        sys.exit(1)
