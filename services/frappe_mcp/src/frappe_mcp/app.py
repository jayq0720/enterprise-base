"""HTTP 入口。

- `POST /v1/session`：Chatflow 的 HTTP 节点带 `sys.user_id` 换一个短期会话令牌
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
from frappe_mcp.tools import Tools

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

    return mcp


def create_app(settings: Settings | None = None, hr: HR | None = None) -> ASGIApp:
    s = settings or Settings()
    # MCP SDK 会把日志级别设成 INFO，httpx 的 INFO 日志带完整请求地址
    # （含员工 userid、邮箱），压到 WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)
    secret = s.mcp_session_secret.get_secret_value().encode()
    client = httpx.AsyncClient(timeout=s.http_timeout_seconds)
    mcp = build_mcp(Tools(s, hr or FrappeHR(s, client)), s.mcp_allowed_hosts)

    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def new_session(request: Request) -> Response:
        try:
            user_id = str(json.loads(await request.body())["user_id"]).strip()
        except (ValueError, KeyError, TypeError):
            user_id = ""
        if not user_id or len(user_id) > 128:
            return JSONResponse({"error": "需要 user_id"}, status_code=422)
        # 不在这里查员工：查不到的人拿到令牌也查不到任何数据
        token = session.issue(secret, user_id, s.session_ttl_seconds)
        return JSONResponse({"session_token": token, "expires_in": s.session_ttl_seconds})

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with client, mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/v1/session", new_session, methods=["POST"]),
            Mount("/mcp", app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    return _Auth(app, s.mcp_service_token.get_secret_value().encode())
