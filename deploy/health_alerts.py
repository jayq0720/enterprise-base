"""只发送组件、数值和UTC时间；记录尝试时间，避免不确定超时导致刷屏。"""

import json
import math
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from backup import private
from release_state import write

PREFIXES = {"container", "http", "disk", "memory", "backup", "scheduler", "queue", "tls"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(url, message):
    payload = json.dumps({"msgtype": "text", "text": {"content": message}}).encode()
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("机器人响应无效")
        result = json.loads(body)
        if type(result.get("errcode")) is not int or result["errcode"] != 0:
            raise ValueError("机器人拒绝告警")


def valid_key(key):
    return (
        isinstance(key, str)
        and re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", key)
        and key.split(".")[0] in PREFIXES
    )


def load(path):
    if not path.exists():
        return {"schema": 1, "issues": {}}
    private(path)
    value = json.loads(path.read_text())
    if (
        set(value) != {"schema", "issues"}
        or value["schema"] != 1
        or not isinstance(value["issues"], dict)
    ):
        raise ValueError("告警状态无效")
    for key, issue in value["issues"].items():
        if not valid_key(key) or set(issue) != {"active", "recovery", "attempt_at"}:
            raise ValueError("告警状态无效")
        if (
            type(issue["active"]) is not bool
            or type(issue["recovery"]) is not bool
            or type(issue["attempt_at"]) not in (int, float)
            or not math.isfinite(issue["attempt_at"])
        ):
            raise ValueError("告警状态类型无效")
    return value


def update(path, results, hook, now, sender=send):
    """调用方持有维护锁；无Webhook只写本机日志，发送失败不泄露地址。"""
    path = Path(path)
    if not math.isfinite(now):
        raise ValueError("检查时间无效")
    for key, (healthy, value) in results.items():
        if (
            not valid_key(key)
            or type(healthy) is not bool
            or (
                value is not None
                and (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or abs(value) > 1_000_000_000
                )
            )
        ):
            raise ValueError("告警只能包含已知检查项和有限数值")
    state = load(path)
    pending = []
    for key, (healthy, value) in results.items():
        issue = state["issues"].get(key)
        if healthy and issue is None:
            continue
        if not healthy and (issue is None or not issue["active"]):
            issue = dict(active=True, recovery=False, attempt_at=now - 3600)
            state["issues"][key] = issue
        elif healthy and issue["active"]:
            issue.update(active=False, recovery=True, attempt_at=now - 3600)
        due = now < issue["attempt_at"] or now - issue["attempt_at"] >= 3600
        if (issue["active"] or issue["recovery"]) and due:
            issue["attempt_at"] = now
            status = "告警" if issue["active"] else "已恢复"
            numeric = "不可用" if value is None else format(value, ".2f")
            stamp = datetime.fromtimestamp(now, UTC).isoformat()
            message = f"企业基础平台 {status}；组件/检查={key}；数值={numeric}；时间={stamp}"
            pending.append((key, message))
    # 发送之前保存尝试时间；崩溃或超时后每小时最多重试一次。
    write(path, state)
    delivered = True
    for key, message in pending:
        try:
            if hook:
                sender(hook, message)
            else:
                print(message)
            state["issues"][key]["recovery"] = False
        except Exception as error:
            print("health:告警投递失败:" + type(error).__name__)
            delivered = False
    write(path, state)
    return delivered
