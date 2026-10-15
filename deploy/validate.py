"""隔离验证部署配置；只运行 Compose config，不构建镜像或启动服务。"""
import copy
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
COMPOSE = shlex.split(os.environ.get("EB_COMPOSE_COMMAND", "docker compose"))
FILES = ("prepare.py", ".env.example", "upstream.lock.json", "apps.lock.json",
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
    for defect in ("image", "ports", "volume"):
        bad = copy.deepcopy(model)
        api = bad["services"]["api"]
        if defect == "image":
            api["image"] = "langgenius/dify-api:latest"
        elif defect == "ports":
            api["ports"] = [{"target": 5001, "published": "5001"}]
        else:
            api["volumes"] = [{"type": "volume", "target": "/app/api/storage"}]
        try:
            audit(bad, "dify")
        except RuntimeError:
            continue
        raise RuntimeError(f"反例未被拒绝：{defect}。")


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
    prepare("wechat", "secrets/.env", success=False)
    # 缺失整个配置项也必须拒绝，不能只检查已有键。
    incomplete = work / "secrets/incomplete.env"
    incomplete.write_text("\n".join(f"{k}=synthetic-test-value" for k in values
                                     if k != "WECHATCOMAPP_TOKEN") + "\n")
    prepare("wechat", str(incomplete), success=False)
    incomplete.unlink()
    stacks = (("edge", "secrets/.env", "docker-compose.yml", "wechat"),
              ("dify", "secrets/dify.env", "data/dify/docker/eb-compose.yaml", None),
              ("frappe", "secrets/.env", "data/frappe/eb-compose.yaml", "bootstrap"))
    for project, domain, scheme in (("eb", "localhost", "http"),
                                    ("eb-ci-review", "example.invalid", "https")):
        values.update(EB_PROJECT=project, EB_DOMAIN=domain, EB_SCHEME=scheme)
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
                require(all(p["host_ip"] == "127.0.0.1" for p in caddy["ports"]), "默认入口不再仅监听本机。")
                require(wechat["logging"]["driver"] == "none" and ">/dev/null 2>&1" in wechat["entrypoint"][-1],
                        "企业微信输出未关闭。")
                require(any(m["source"] == "/dev/null" and m["target"] == "/app/run.log"
                            for m in wechat["volumes"]), "企业微信文件日志未丢弃。")
            elif stack == "dify":
                require("qdrant" in services and "weaviate" not in services, "向量库不是 Qdrant。")
                api = services["api"]["environment"]
                require(api["VECTOR_STORE"] == "qdrant" and api["CODE_EXECUTION_API_KEY"] == values["SANDBOX_API_KEY"],
                        "Dify 向量配置或 Sandbox 密钥不一致。")
                require(services["sandbox"]["environment"]["API_KEY"] == values["SANDBOX_API_KEY"], "Sandbox 密钥未传递。")
                require(set(services["sandbox"]["networks"]) == {"ssrf_proxy_network"}
                        and model["networks"]["ssrf_proxy_network"]["internal"], "Sandbox 隔离网络被放宽。")
                reject_mutations(model)
            else:
                require(services["frontend"]["environment"]["FRAPPE_SITE_NAME_HEADER"] == f"hr.{domain}",
                        "Frappe 站点域名未传递。")
                require("edge" not in services["backend"]["networks"], "Frappe 后端直接接入入口网络。")
                command_text = " ".join(services["create-site"]["command"])
                require("frappe.client.set_value" in command_text and '"enable_telemetry":0' in command_text,
                        "Frappe 未设置实际 System Settings 遥测开关。")
            print(f"{project}/{stack}：配置通过，镜像固定、数据卷命名、端口边界正确。")
    require(all(values[k] not in output for k in secret_keys), "初始化或生成输出包含密钥。")
    require(all(f.stat().st_mode & 0o777 == 0o600 for f in (work / "secrets").iterdir()), "密钥文件权限不是 600。")
    require((work / "secrets").stat().st_mode & 0o777 == 0o700, "密钥目录权限不是 700。")
    print("初始化幂等、密钥权限与缺项拒绝通过；三类配置反例均被拒绝。")


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
