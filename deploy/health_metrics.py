"""主机与数据卷指标；只返回有限数值，不读取员工数据和日志。"""

import json
import math
import re
import socket
import ssl
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4

from backup import private

LIMITS = {
    "DISK_MIN_PERCENT": (15, 0, 100),
    "MEMORY_MIN_PERCENT": (10, 0, 100),
    "BACKUP_MAX_HOURS": (26, 0.01, 8760),
    "QUEUE_MAX": (100, 0, 1000000),
    "CERT_MIN_DAYS": (14, 0, 3650),
    "HTTP_TIMEOUT": (10, 0.1, 60),
}
DISK = """import os,sys
values=[]
for path in sys.argv[1:]:
    s=os.statvfs(path)
    values.append(s.f_bavail / s.f_blocks * 100)
print(min(values))
"""


def limits(config):
    result = {}
    for key, (default, low, high) in LIMITS.items():
        value = float(config.get("EB_HEALTH_" + key, default))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError("健康检查阈值无效")
        result[key] = value
    return result


def memory(path=Path("/proc/meminfo")):
    data = dict(line.split(":", 1) for line in path.read_text().splitlines())
    total, available = (int(data[key].split()[0]) for key in ("MemTotal", "MemAvailable"))
    if not 0 <= available <= total or total <= 0:
        raise ValueError("内存数值无效")
    return available / total * 100


def backup_age(deploy, config, now):
    directory = Path(deploy) / (config.get("EB_BACKUP_DIR") or "data/backups")
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError("备份目录不能是链接")
    private(directory, True)
    pattern = re.compile(re.escape(config["EB_PROJECT"]) + r"-(\d{8}T\d{6}Z)-[a-f0-9]{8}\.tar\.age")
    stamps = []
    for path in directory.iterdir():
        match = pattern.fullmatch(path.name)
        if match:
            private(path)
            if path.stat().st_size:
                stamps.append(datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))
    if not stamps:
        raise ValueError("没有备份")
    # 以备份名中的UTC时间计算，复制旧文件不会被当作新备份。
    hours = (now - max(stamps).timestamp()) / 3600
    if hours < 0:
        raise ValueError("备份时间在未来")
    return hours


def disk(containers, project, run):
    names = sorted(
        {
            m["Name"]
            for item in containers.values()
            for m in item.get("Mounts", [])
            if m["Type"] == "volume"
        }
    )
    if not names:
        raise ValueError("缺少数据卷")
    records = json.loads(run(["docker", "volume", "inspect", *names]))
    owners = {project, project + "-hr", project + "-dify"}
    if len(records) != len(names) or any(
        v["Name"] not in names
        or (v.get("Labels") or {}).get("com.docker.compose.project") not in owners
        for v in records
    ):
        raise ValueError("数据卷归属无效")
    image = containers["hr", "backend"]["Image"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("探针镜像无效")
    name = "eb-health-" + uuid4().hex
    args = [
        "docker",
        "create",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--log-driver",
        "none",
        "--user",
        "0",
    ]
    paths = []
    for index, volume in enumerate(names):
        path = f"/volumes/{index}"
        paths.append(path)
        args += ["--mount", f"type=volume,source={volume},target={path},readonly,volume-nocopy"]
    args += ["--entrypoint", "/home/frappe/frappe-bench/env/bin/python", image, "-c", DISK, *paths]
    created = False
    try:
        run(args)
        created = True
        return float(run(["docker", "start", "-a", name]))
    finally:
        if created:
            run(["docker", "rm", "-f", name])


def certificate(config, kind, now, timeout):
    address = config["EB_BIND"]
    address = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(address, address)
    with socket.create_connection((address, 443), timeout=timeout) as connection:
        with ssl.create_default_context().wrap_socket(
            connection, server_hostname=kind + "." + config["EB_DOMAIN"]
        ) as secure:
            certificate = secure.getpeercert() or {}
            expiry = certificate.get("notAfter")
            if not isinstance(expiry, str):
                raise ValueError("证书没有有效期")
            expires = ssl.cert_time_to_seconds(expiry)
    return (expires - now) / 86400


def check(config, deploy, containers, run, now):
    thresholds, results = limits(config), {}
    probes = [
        ("memory", lambda: memory(), "MEMORY_MIN_PERCENT", True),
        ("disk", lambda: disk(containers, config["EB_PROJECT"], run), "DISK_MIN_PERCENT", True),
        ("backup", lambda: backup_age(deploy, config, now), "BACKUP_MAX_HOURS", False),
    ]
    if config["EB_SCHEME"] == "https":
        probes += [
            (
                "tls." + kind,
                partial(certificate, config, kind, now, thresholds["HTTP_TIMEOUT"]),
                "CERT_MIN_DAYS",
                True,
            )
            for kind in ("dify", "hr", "wecom")
        ]
    for name, probe, threshold, minimum in probes:
        try:
            value = probe()
            if not math.isfinite(value) or abs(value) > 1000000000:
                raise ValueError("指标无效")
            results[name] = (
                value >= thresholds[threshold] if minimum else value <= thresholds[threshold],
                value,
            )
        except Exception:
            results[name] = (False, None)
    return results
