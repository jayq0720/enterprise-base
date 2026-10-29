"""make health入口；维护中跳过，异常只打印类别。"""

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import backup
import health_alerts
import health_metrics
import health_services
from restore_workspace import values

ROOT = Path(__file__).resolve().parent


def check(env):
    path = Path(env).absolute()
    if path != ROOT / "secrets/.env" or path.is_symlink():
        raise ValueError("只检查当前部署")
    backup.private(ROOT / "secrets", True)
    backup.private(path)
    config = values(path)
    if config["EB_SCHEME"] not in {"http", "https"}:
        raise ValueError("协议无效")
    hook = config.get("EB_ALERT_WECOM_WEBHOOK", "")
    parsed = urlsplit(hook)
    if hook and (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("告警地址必须为HTTPS")
    thresholds = health_metrics.limits(config)
    profiles = set(filter(None, config.get("EB_HEALTH_PROFILES", "").split(",")))
    with backup.operation():
        containers, results = health_services.inventory(ROOT, config, profiles)
        results.update(health_services.check(containers, config, thresholds))
        now = time.time()
        results.update(health_metrics.check(config, ROOT, containers, health_services.command, now))
        for name, (healthy, numeric) in sorted(results.items()):
            value = "不可用" if numeric is None else format(numeric, ".2f")
            print(f"health:{name}:{'通过' if healthy else '失败'}:{value}")
        health_alerts.update(ROOT / "data/health-state.json", results, hook, now)


def main(argv=None):
    os.umask(0o077)
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1:
            raise ValueError("需要私有环境配置")
        check(args[0])
    except BlockingIOError:
        print("health:维护中:跳过本次检查")
    except Exception as error:
        print("health:失败:" + type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
