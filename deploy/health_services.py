"""只读检查三套Compose、内部HTTP及Frappe后台任务；不输出inspect原文。"""

import json
import os
import re
import subprocess
from pathlib import Path

from release_runtime import MODELS
from restore_workspace import values

LABEL = "com.docker.compose."
ONESHOTS = {"configurator", "create-site", "init_permissions"}
HTTP = """import sys,urllib.request
request=urllib.request.Request(sys.argv[1], headers={'Host':sys.argv[2]})
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(request,timeout=float(sys.argv[3])) as response:
    print(response.status)
"""
SCHEDULER = """import frappe,json,sys
from frappe.utils.scheduler import is_scheduler_inactive,is_schduler_process_running
from frappe.utils.background_jobs import get_queue
frappe.init(site=sys.argv[1])
try:
    frappe.connect()
    enabled=not is_scheduler_inactive(verbose=False) and is_schduler_process_running()
    count=sum(get_queue(q).count for q in ('short','default','long'))
    print(json.dumps([enabled,count]))
finally:
    frappe.destroy()
"""


def command(args, *, env=None):
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        timeout=90,
        check=True,
        env=env,
    ).stdout


def inventory(deploy, config, profiles, run=command):
    deploy, project = Path(deploy), config["EB_PROJECT"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
        raise ValueError("项目名无效")
    if not profiles <= {"wechat", "policy-sync", "hr-assistant"}:
        raise ValueError("监控profiles无效")
    keys = set(config) | set(values(deploy / "secrets/dify.env"))
    clean = {k: v for k, v in os.environ.items() if k not in keys and not k.startswith("COMPOSE_")}
    containers: dict = {}
    results: dict[str, tuple[bool, int | None]] = {}
    for stack, suffix, filename, envfile in MODELS:
        try:
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
                raise ValueError("项目不符")
            ids = (
                run(
                    [
                        "docker",
                        "ps",
                        "-aq",
                        "--no-trunc",
                        "--filter",
                        "label=" + LABEL + "project=" + project + suffix,
                    ]
                )
                .decode()
                .split()
            )
            records = json.loads(run(["docker", "inspect", *ids])) if ids else []
            actual = {}
            for item in records:
                labels = item["Config"]["Labels"] or {}
                name = labels[LABEL + "service"]
                if labels[LABEL + "project"] != project + suffix or not re.fullmatch(
                    r"[a-z0-9_-]+", name
                ):
                    raise ValueError("容器归属不符")
                if labels.get(LABEL + "oneoff", "false").lower() == "true":
                    continue
                if name not in model["services"] or name in actual:
                    raise ValueError("容器配置不符")
                actual[name] = item
                containers[stack, name] = item
            expected = {
                n
                for n, s in model["services"].items()
                if not s.get("profiles") or set(s["profiles"]) & profiles
            } | set(actual)
            for name in expected:
                state = actual.get(name, {}).get("State", {})
                if name in ONESHOTS:
                    healthy = state.get("Status") == "exited" and state.get("ExitCode") == 0
                else:
                    healthy = bool(state.get("Running")) and not (
                        state.get("Paused") or state.get("Restarting")
                    )
                    health = model["services"][name].get("healthcheck", {})
                    if health and not health.get("disable") and health.get("test") != ["NONE"]:
                        healthy = healthy and state.get("Health", {}).get("Status") == "healthy"
                    elif state.get("Health"):
                        healthy = healthy and state["Health"]["Status"] == "healthy"
                results[f"container.{stack}.{name}"] = (healthy, int(healthy))
            results[f"container.{stack}.inventory"] = (True, 1)
        except Exception:
            results[f"container.{stack}.inventory"] = (False, None)
    return containers, results


def check(containers, config, thresholds, run=command):
    results: dict[str, tuple[bool, int | None]] = {}
    domain = config["EB_DOMAIN"]
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
        if ("hr", name) in containers
    ]
    for stack, name, python, url, host in probes:
        try:
            identity = containers[stack, name]["Id"]
            status = int(
                run(
                    [
                        "docker",
                        "exec",
                        identity,
                        python,
                        "-c",
                        HTTP,
                        url,
                        host,
                        str(thresholds["HTTP_TIMEOUT"]),
                    ]
                )
            )
            results["http." + name] = (200 <= status < 300, status)
        except Exception:
            results["http." + name] = (False, None)
    try:
        site = config.get("EB_RESTORE_PRIMARY_SITE") or "hr." + domain
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", site):
            raise ValueError("站点无效")
        # scheduler进程锁位于本容器，不能在backend里检查该锁。
        identity = containers["hr", "scheduler"]["Id"]
        active, count = json.loads(
            run(
                [
                    "docker",
                    "exec",
                    "--workdir",
                    "/home/frappe/frappe-bench/sites",
                    identity,
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    SCHEDULER,
                    site,
                ]
            )
        )
        if type(active) is not bool or type(count) is not int or not 0 <= count <= 1000000000:
            raise ValueError("后台任务状态无效")
        results["scheduler"] = (active, int(active))
        results["queue"] = (count <= thresholds["QUEUE_MAX"], count)
    except Exception:
        results.update(scheduler=(False, None), queue=(False, None))
    return results
