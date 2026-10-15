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

ROOT = Path(__file__).resolve().parent
COMPOSE = shlex.split(os.environ.get("EB_COMPOSE_COMMAND", "docker compose"))
FILES = ("prepare.py", ".env.example", "upstream.lock.json", "apps.lock.json", "caddy.lock.json",
         "docker-compose.yml", "frappe-services.yaml", "Caddyfile")


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
    for defect in ("image", "ports", "volume", "nginx", "plugin_log", "worker_log", "agent_key"):
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
        else:
            bad["services"]["agent_backend"]["environment"]["DIFY_AGENT_SERVER_SECRET_KEY"] = "a" * 64
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
    secret_keys = [k for k, v in settings(work / ".env.example").items() if v == "CHANGE_ME"]
    require(len({values[k] for k in secret_keys}) == len(secret_keys), "服务未使用独立随机密钥。")
    prepare("check", "secrets/.env")
    prepare("check", ".env.example", success=False)
    malformed = work / "secrets/malformed.env"
    for key in ("a" * 64, values["DIFY_AGENT_SERVER_SECRET_KEY"] + "=", "invalid"):
        malformed.write_text("".join(f"{k}={key if k == 'DIFY_AGENT_SERVER_SECRET_KEY' else v}\n" for k, v in values.items()))
        prepare("check", str(malformed), success=False)
        prepare(str(malformed), success=False)
    malformed.unlink()
    prepare("wechat", "secrets/.env", success=False)
    # 缺失整个配置项也必须拒绝，不能只检查已有键。
    incomplete = work / "secrets/incomplete.env"
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
    stacks = (("edge", "secrets/.env", "docker-compose.yml", "wechat"),
              ("dify", "secrets/dify.env", "data/dify/docker/eb-compose.yaml", None),
              ("frappe", "secrets/.env", "data/frappe/eb-compose.yaml", "bootstrap"))
    for project, domain, scheme, bind, cidrs in (("eb", "localhost", "http", "127.0.0.1", "127.0.0.1/32"),
            ("eb-ci-review", "example.invalid", "https", "0.0.0.0", "203.0.113.0/24 2001:db8::/64")):
        values.update(EB_PROJECT=project, EB_DOMAIN=domain, EB_SCHEME=scheme, EB_BIND=bind, EB_ADMIN_ALLOW_CIDRS=cidrs)
        private.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        output += prepare("secrets/.env")
        for stack, environment, file, profile in stacks:
            command = [*COMPOSE, "--env-file", environment, "-f", file]
            if profile:
                command += ["--profile", profile]
            run([*command, "config", "--quiet"], work)
            rendered = run([*command, "config", "--format", "json"], work)
            model = json.loads(rendered)
            audit(model, stack)
            require(model["name"] == project + {"edge": "", "dify": "-dify", "frappe": "-hr"}[stack],
                    "项目名未正确传递。")
            services = model["services"]
            if stack == "edge":
                caddy, wechat = services["caddy"], services["wechat"]
                require(caddy["environment"]["DIFY_ADDRESS"] == f"{scheme}://dify.{domain}",
                        "域名/协议未正确传递到入口。")
                require(all(p["host_ip"] == bind for p in caddy["ports"]), "入口绑定地址未正确传递。")
                require(caddy["environment"]["EB_ADMIN_ALLOW_CIDRS"] == cidrs, "管理员白名单未正确传递。")
                verify_access(work, caddy["environment"])
                unguarded = (work / "Caddyfile").read_text().replace("import admin_only", "")
                try:
                    verify_access(work, caddy["environment"], unguarded, run_probe=False)
                except RuntimeError:
                    pass
                else:
                    raise RuntimeError("删除后台来源限制后未被拒绝。")
                print(f"{project}/caddy：后台允许/403、伪造头拒绝、回调边界及删除限制反例通过。")
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
                require(services["frontend"]["environment"]["FRAPPE_SITE_NAME_HEADER"] == f"hr.{domain}",
                        "Frappe 站点域名未传递。")
                require("edge" not in services["backend"]["networks"], "Frappe 后端直接接入入口网络。")
                check_bootstrap(services["create-site"], work)
            print(f"{project}/{stack}：配置通过，镜像固定、数据卷命名、端口边界正确。")
    print("四类启动兼容性反例被拒绝；建站参数与失败停止检查通过。")
    require(all(values[k] not in output for k in secret_keys), "初始化或生成输出包含密钥。")
    require(all(f.stat().st_mode & 0o777 == 0o600 for f in (work / "secrets").iterdir()), "密钥文件权限不是 600。")
    require((work / "secrets").stat().st_mode & 0o777 == 0o700, "密钥目录权限不是 700。")
    print("初始化幂等、密钥权限与缺项拒绝通过；无效白名单、三类配置反例均被拒绝。")


def main():
    require(shutil.which(COMPOSE[0]) is not None, "找不到 Compose 校验程序。")
    with tempfile.TemporaryDirectory(prefix="eb-validation-") as temp:
        work = Path(temp) / "deploy"
        work.mkdir()
        for name in FILES:
            shutil.copyfile(ROOT / name, work / name)
        config = Path(temp) / "config/dify-on-wechat"
        config.mkdir(parents=True)
        shutil.copyfile(ROOT.parent / "config/dify-on-wechat/config.json", config / "config.json")
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
