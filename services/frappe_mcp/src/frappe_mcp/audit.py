"""工具调用审计：每次调用记一条（谁、哪个入口、哪个工具、结果、操作了哪张单）。

同时写两处：本服务日志（logger frappe_mcp.audit，一行 JSON，docker logs 可查）和
Frappe 的“EB Audit Log”（HR 经理、系统管理员在后台只读查看）。只记标识和结果，
不记请假事由、跟进内容、客户联系方式、令牌。写 Frappe 失败只记日志，不影响员工操作。
"""

import contextvars
import json
import logging
import re
from typing import Any, Protocol

import httpx

log = logging.getLogger("frappe_mcp.audit")
# 入口：Dify（MCP 工具和确认请假接口）、员工前台、外部进件；由 app.py 在路由里设
CHANNEL: contextvars.ContextVar[str] = contextvars.ContextVar("eb_audit_channel", default="dify")
_SAFE = re.compile(r"[^A-Za-z0-9_.@:-]")


class Sink(Protocol):
    async def audit(self, entry: dict[str, str]) -> None: ...


def _clean(value: object) -> str:
    return _SAFE.sub("", str(value or ""))[:140]


async def record(
    sink: Sink,
    tool: str,
    outcome: str,
    *,
    userid: str = "",
    employee: str = "",
    target: str = "",
    reason: str = "",
) -> None:
    entry = {
        "channel": CHANNEL.get(),
        "tool": tool,
        "outcome": outcome,
        "reason": _clean(reason),
        "userid": _clean(userid),
        "employee": _clean(employee),
        "target": _clean(target),
    }
    log.info(json.dumps(entry, ensure_ascii=False))
    try:
        await sink.audit(entry)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("audit not saved to frappe: %s", type(e).__name__)


def outcome_of(result: dict[str, Any]) -> str:
    """工具返回值 → 审计结果：有 error 或 ok 为 False 记 rejected，否则 ok。"""
    return "rejected" if result.get("ok") is False or "error" in result else "ok"
