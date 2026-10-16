"""HTTP 入口。

- `POST /v1/session`：Chatflow 的 HTTP 节点带 `sys.user_id` 换一个短期会话令牌
- `POST /v1/leave/confirm`：员工回复“确认请假”后，Chatflow 的 HTTP 节点带 `sys.user_id`
  和请假草稿调用，提交请假单。这个接口不是 MCP 工具，模型调不到
- `/mcp`：MCP（streamable HTTP），Dify 的 MCP 服务配置带固定 Authorization 头
- `GET /healthz`

所有工具都要求传入会话令牌，只查令牌里那个员工自己的数据。
"""

import contextlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from frappe_mcp import session
from frappe_mcp.config import Settings
from frappe_mcp.frappe import HR, FrappeHR
from frappe_mcp.tools import CONFIRM_WORD, Tools

SESSION_HINT = "会话令牌，由工作流在调用前填好，不要自己编写或修改。"


class _Auth:
    """除 /healthz 外都要求服务 Token。"""

    def __init__(self, app: ASGIApp, token: bytes) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            headers = dict(scope["headers"])
            scheme, _, got = headers.get(b"authorization", b"").decode().partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(got.encode(), self._token):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self._app(scope, receive, send)


def build_mcp(tools: Tools, allowed_hosts: list[str]) -> FastMCP:
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts
    )
    mcp = FastMCP(
        "enterprise-base-hr", stateless_http=True, json_response=True, transport_security=security
    )
    mcp.settings.streamable_http_path = "/"

    @mcp.tool(
        description=f"查询提问人自己的各类假期余额（总额、已用、审批中、剩余）。{SESSION_HINT}"
    )
    async def leave_balance(session_token: str) -> dict[str, Any]:
        return await tools.leave_balance(session_token)

    @mcp.tool(description=f"查询提问人自己最近的请假申请和审批进度。{SESSION_HINT}")
    async def my_leave_applications(session_token: str) -> dict[str, Any]:
        return await tools.my_leave_applications(session_token)

    @mcp.tool(description=f"查询等待提问人审批的请假申请。{SESSION_HINT}")
    async def pending_my_approval(session_token: str) -> dict[str, Any]:
        return await tools.pending_my_approval(session_token)

    @mcp.tool(description=f"查询提问人自己的岗位和岗位职责。{SESSION_HINT}")
    async def my_responsibilities(session_token: str) -> dict[str, Any]:
        return await tools.my_responsibilities(session_token)

    @mcp.tool(
        description="帮提问人起草请假申请（只起草，不会提交）。leave_type 用假期类型名称，"
        "日期写成 YYYY-MM-DD，reason 是请假事由。返回草稿后把内容念给员工核对，"
        f"告诉他回复“{CONFIRM_WORD}”才会提交；不要替员工确认。{SESSION_HINT}"
    )
    async def draft_leave(
        session_token: str, leave_type: str, from_date: str, to_date: str, reason: str = ""
    ) -> dict[str, Any]:
        return await tools.draft_leave(session_token, leave_type, from_date, to_date, reason)

    return mcp


def _json_fields(raw: bytes, *names: str) -> list[str] | None:
    try:
        data = json.loads(raw)
        values = [str(data[n]).strip() for n in names]
    except (ValueError, KeyError, TypeError):
        return None
    if not all(values) or any(len(v) > 4096 for v in values):
        return None
    return values


def create_app(settings: Settings | None = None, hr: HR | None = None) -> ASGIApp:
    s = settings or Settings()
    # MCP SDK 会把日志级别设成 INFO，httpx 的 INFO 日志带完整请求地址
    # （含员工 userid、邮箱），压到 WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)
    secret = s.mcp_session_secret.get_secret_value().encode()
    client = httpx.AsyncClient(timeout=s.http_timeout_seconds)
    tools = Tools(s, hr or FrappeHR(s, client))
    mcp = build_mcp(tools, s.mcp_allowed_hosts)

    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def new_session(request: Request) -> Response:
        fields = _json_fields(await request.body(), "user_id")
        if fields is None or len(fields[0]) > 128:
            return JSONResponse({"error": "需要 user_id"}, status_code=422)
        user_id = fields[0]
        # 不在这里查员工：查不到的人拿到令牌也查不到任何数据
        token = session.issue(secret, user_id, s.session_ttl_seconds)
        return JSONResponse({"session_token": token, "expires_in": s.session_ttl_seconds})

    async def confirm_leave(request: Request) -> Response:
        fields = _json_fields(await request.body(), "user_id", "draft_token")
        if fields is None:
            return JSONResponse({"error": "需要 user_id 和 draft_token"}, status_code=422)
        return JSONResponse(await tools.confirm_leave(*fields))

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with client, mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/v1/session", new_session, methods=["POST"]),
            Route("/v1/leave/confirm", confirm_leave, methods=["POST"]),
            Mount("/mcp", app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    return _Auth(app, s.mcp_service_token.get_secret_value().encode())
