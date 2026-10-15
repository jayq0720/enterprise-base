"""用固定版官方 Caddy 验证访问限制；后端替换为测试响应，不访问业务服务。"""
import copy
import hashlib
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import tarfile
import time
import urllib.request
from urllib.parse import urlsplit


def need(condition):
    if not condition:
        raise RuntimeError("Caddy 来源限制或允许/拒绝回归检查失败。")


def binary_for(work):
    lock = json.loads((work / "caddy.lock.json").read_text())
    supplied = os.environ.get("EB_CADDY_BINARY")
    binary = Path(supplied) if supplied else work / "caddy-check-tool"
    if not binary.exists():
        need(not supplied)
        system = {"Darwin": "mac", "Linux": "linux"}.get(platform.system())
        arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "AMD64": "amd64"}.get(platform.machine())
        target = f"{system}_{arch}"
        need(target in lock["sha256"])
        url = f'https://github.com/caddyserver/caddy/releases/download/v{lock["version"]}/caddy_{lock["version"]}_{target}.tar.gz'
        with urllib.request.urlopen(url, timeout=45) as response:
            archive = response.read()
        need(hashlib.sha256(archive).hexdigest() == lock["sha256"][target])
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
            member = source.extractfile("caddy")
            need(member is not None)
            binary.write_bytes(member.read())
        binary.chmod(0o700)
    version = subprocess.run([str(binary), "version"], capture_output=True, text=True, check=True)
    need(version.stdout.split()[0] == "v" + lock["version"])
    return str(binary)


def flatten(routes):
    for route in routes:
        handles = route["handle"]
        if not route.get("match") and len(handles) == 1 and handles[0]["handler"] == "subroute":
            yield from flatten(handles[0]["routes"])
        else:
            yield route


def check_routes(config, environment):
    routes = [r for s in config["apps"]["http"]["servers"].values() for r in s["routes"]]
    for key, upstream in (("DIFY_ADDRESS", "dify:80"), ("HR_ADDRESS", "frappe:8080")):
        host = urlsplit(environment[key]).hostname
        sites = [r for r in routes if r.get("match") == [{"host": [host]}]]
        need(len(sites) == 1 and len(sites[0]["handle"]) == 1)
        chain = list(flatten(sites[0]["handle"][0]["routes"]))
        need(len(chain) == 2)
        deny, proxy = chain
        need(deny.get("match") == [{"not": [{"remote_ip": {"ranges": environment["EB_ADMIN_ALLOW_CIDRS"].split()}}]}])
        need(deny["handle"] == [{"handler": "static_response", "status_code": 403}])
        need(not proxy.get("match") and proxy["handle"][0]["handler"] == "reverse_proxy")
        need(proxy["handle"][0]["upstreams"] == [{"dial": upstream}])
    return routes


def probe(binary, routes, environment, work):
    def replace_backend(node):
        if isinstance(node, dict):
            if node.get("handler") == "reverse_proxy":
                node.clear()
                node.update(handler="static_response", status_code=200, body="test-backend")
            for value in node.values():
                replace_backend(value)
        elif isinstance(node, list):
            for value in node:
                replace_backend(value)

    routes = copy.deepcopy(routes)
    replace_backend(routes)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    config = {"admin": {"disabled": True, "config": {"persist": False}}, "apps": {"http": {"servers": {
        "probe": {"listen": [f"127.0.0.1:{port}"], "routes": routes, "automatic_https": {"disable": True}}
    }}}}
    path = work / "caddy-probe.json"
    path.write_text(json.dumps(config))
    process = subprocess.Popen([binary, "run", "--config", str(path)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env={**os.environ,
                               "XDG_CONFIG_HOME": str(work / "caddy-config"), "XDG_DATA_HOME": str(work / "caddy-data")})
    def status(host, path, forged=False):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            headers = {"Host": host}
            if forged:
                headers.update({"X-Forwarded-For": "203.0.113.42", "X-Real-IP": "203.0.113.42",
                                "Forwarded": "for=203.0.113.42"})
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()
    try:
        ready = False
        for _ in range(50):
            if process.poll() is not None:
                break
            try:
                status("wecom.example.invalid", "/not-a-callback")
                ready = True
                break
            except OSError:
                time.sleep(0.1)
        need(ready)
        allowed = any(ipaddress.ip_address("127.0.0.1") in ipaddress.ip_network(cidr)
                      for cidr in environment["EB_ADMIN_ALLOW_CIDRS"].split())
        for key, paths in (("DIFY_ADDRESS", ("/", "/install", "/console/api/setup", "/v1/chat-messages")),
                           ("HR_ADDRESS", ("/", "/login", "/api/resource/Employee"))):
            for route in paths:
                for forged in (False, True):
                    need(status(urlsplit(environment[key]).hostname, route, forged) == (200 if allowed else 403))
        host = urlsplit(environment["WECOM_ADDRESS"]).hostname
        need(status(host, "/wxcomapp") == 200 and status(host, "/not-a-callback") == 404)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def verify_access(work, environment, text=None, run_probe=True):
    binary = binary_for(work)
    path = work / "caddy-adapt.Caddyfile"
    path.write_text((work / "Caddyfile").read_text() if text is None else text)
    result = subprocess.run([binary, "adapt", "--config", str(path), "--adapter", "caddyfile"],
                            env={**os.environ, **environment}, capture_output=True, text=True)
    need(result.returncode == 0)
    config = json.loads(result.stdout)
    routes = check_routes(config, environment)
    if run_probe:
        probe(binary, routes, environment, work)
