"""HTTP 入口。

- `GET /auth/login`、`GET /auth/callback`：企业微信扫码登录（在企业微信里打开时免扫码），
  拿到 userid 后到 X4 确认是在职员工，签发会话 Cookie
- `POST /auth/logout`
- `/api/*`：页面调用的接口。身份只取会话 Cookie 里的 userid，由服务端带给 X4、X3、Dify；
  浏览器拿不到任何服务 Token 或 Dify Key，传来的身份字段一律不用
- `GET /healthz`、`/` 和 `/static/*` 页面文件
"""

import contextlib
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from portal import session
from portal.backends import Api, Backends, Unavailable
from portal.config import Settings

STATIC = Path(__file__).with_name("static")
COOKIE = "__Host-eb_portal"
LOGIN_COOKIE = "__Host-eb_portal_login"
RANDOM = re.compile(r"[A-Za-z0-9_-]{43}\Z")
CONVERSATION = re.compile(r"[0-9a-fA-F-]{36}\Z")
UNAVAILABLE = "系统暂时不可用，请稍后再试。"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
# 页面能调的 X4 功能：读的用 GET，写的用 POST；参数只收这些字段
READS = {
    "/api/leave/balance": "leave_balance",
    "/api/leave/applications": "my_leave_applications",
    "/api/leave/types": "leave_types",
    "/api/approvals": "pending_my_approval",
    "/api/duties": "my_responsibilities",
}
WRITES = {
    "/api/leave/draft": ("draft_leave", ("leave_type", "from_date", "to_date", "reason")),
    "/api/leave/confirm": ("confirm_leave", ("draft_token",)),
    "/api/approvals/decide": ("decide", ("leave", "action")),
}


class _Headers:
    """所有响应加安全头；接口和登录不缓存。"""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path: str = scope["path"]

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers += [
                    (b"content-security-policy", CSP.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                ]
                if not path.startswith("/static/"):
                    headers.append((b"cache-control", b"no-store"))
            await send(message)

        await self._app(scope, receive, wrapped)


def create_app(settings: Settings | None = None, backends: Api | None = None) -> ASGIApp:
    s = settings or Settings()
    # httpx 的 INFO 日志带完整请求地址（含 userid、扫码 code），压到 WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)
    secret = s.session_secret.get_secret_value().encode()
    client = httpx.AsyncClient(timeout=s.http_timeout_seconds)
    api: Api = backends or Backends(s, client)
    callback = s.origin + "/auth/callback"
    # userid → (上次确认的员工信息, 确认时间)；只是缓存，丢了就回 X4 再查
    checked: dict[str, tuple[dict[str, Any], float]] = {}

    def _redirect(location: str) -> RedirectResponse:
        return RedirectResponse(location, status_code=303)

    async def login(request: Request) -> Response:
        nonce = secrets.token_urlsafe(32)
        state = session.sign(secret, session.STATE, {"n": session.digest(nonce)}, 300)
        common = {"appid": s.wecom_corp_id, "agentid": s.wecom_agent_id}
        if "wxwork" in request.headers.get("user-agent", "").lower():
            # 在企业微信里打开：网页授权，员工不用再扫码
            query = urlencode(
                {
                    **common,
                    "redirect_uri": callback,
                    "response_type": "code",
                    "scope": "snsapi_base",
                    "state": state,
                }
            )
            target = f"{s.wecom_oauth_url}?{query}#wechat_redirect"
        else:
            query = urlencode(
                {"login_type": "CorpApp", **common, "redirect_uri": callback, "state": state}
            )
            target = f"{s.wecom_sso_url}?{query}"
        resp = _redirect(target)
        resp.set_cookie(
            LOGIN_COOKIE, nonce, max_age=300, secure=True, httponly=True, samesite="lax"
        )
        return resp

    async def login_callback(request: Request) -> Response:
        denied = _redirect("/?login=failed")
        denied.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="lax")
        args = request.query_params
        code, state = args.get("code", ""), args.get("state", "")
        nonce = request.cookies.get(LOGIN_COOKIE, "")
        if (
            set(args.keys()) != {"code", "state"}
            or len(args.multi_items()) != 2
            or not 0 < len(code.encode()) <= 512
            or not RANDOM.fullmatch(nonce)
        ):
            return denied
        try:
            data = session.unsign(secret, session.STATE, state)
        except session.SessionError:
            return denied
        if not secrets.compare_digest(str(data.get("n", "")), session.digest(nonce)):
            return denied
        userid = await api.wecom_userid(code)
        if not userid:
            return denied
        try:
            profile = await api.x4("profile", userid)
        except Unavailable:
            return denied
        if profile.get("ok") is not True:
            not_employee = _redirect("/?login=denied")
            not_employee.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="lax")
            return not_employee
        checked[userid] = (profile, time.monotonic())
        resp = _redirect("/")
        resp.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="lax")
        token = session.sign(secret, session.LOGIN, {"uid": userid}, s.session_ttl_seconds)
        resp.set_cookie(
            COOKIE,
            token,
            max_age=s.session_ttl_seconds,
            secure=True,
            httponly=True,
            samesite="strict",
        )
        return resp

    async def logout(request: Request) -> Response:
        if (err := _csrf(request)) is not None:
            return err
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict")
        return resp

    def _csrf(request: Request) -> Response | None:
        """接口只接受本站页面发起的请求：自定义请求头 + 同源 Origin（写操作）。"""
        if request.headers.get("x-eb-portal") != "1":
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.method != "GET" and request.headers.get("origin") != s.origin:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return None

    async def _current(request: Request) -> tuple[str, dict[str, Any]] | Response:
        if (err := _csrf(request)) is not None:
            return err
        signed_out = JSONResponse({"error": "请先登录"}, status_code=401)
        try:
            uid = str(session.unsign(secret, session.LOGIN, request.cookies.get(COOKIE, ""))["uid"])
        except (session.SessionError, KeyError):
            return signed_out
        cached = checked.get(uid)
        if cached and time.monotonic() - cached[1] < s.recheck_seconds:
            return uid, cached[0]
        try:
            profile = await api.x4("profile", uid)
        except Unavailable:
            return JSONResponse({"error": UNAVAILABLE}, status_code=503)
        if profile.get("ok") is not True:
            # 离职、停用或查不到了：会话立即作废
            checked.pop(uid, None)
            signed_out.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict")
            return signed_out
        checked[uid] = (profile, time.monotonic())
        return uid, profile

    def endpoint(
        handler: Callable[[Request, str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> Callable[[Request], Awaitable[Response]]:
        async def run(request: Request) -> Response:
            current = await _current(request)
            if isinstance(current, Response):
                return current
            try:
                return JSONResponse(await handler(request, *current))
            except Unavailable:
                return JSONResponse({"error": UNAVAILABLE}, status_code=503)

        return run

    async def _body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _text(value: object, limit: int) -> str:
        return value.strip()[:limit] if isinstance(value, str) else ""

    async def me(_: Request, uid: str, profile: dict[str, Any]) -> dict[str, Any]:
        chat = {"hr": s.dify_hr_app_key is not None, "policy": s.dify_policy_app_key is not None}
        keys = ("employee_name", "designation", "features", "notice")
        return {**{k: profile[k] for k in keys if k in profile}, "chat": chat}

    async def read(request: Request, uid: str, _: dict[str, Any]) -> dict[str, Any]:
        return await api.x4(READS[request.url.path], uid)

    async def write(request: Request, uid: str, _: dict[str, Any]) -> dict[str, Any]:
        name, fields = WRITES[request.url.path]
        body = await _body(request)
        return await api.x4(name, uid, **{f: _text(body.get(f), 2048) for f in fields})

    async def search(request: Request, uid: str, _: dict[str, Any]) -> dict[str, Any]:
        query = _text((await _body(request)).get("query"), 500)
        if not query:
            return {"records": [], "notice": "请输入要查的内容。"}
        result = await api.search(uid, query)
        records = [
            {k: str(r.get(k) or "") for k in ("title", "content", "space")}
            for r in result.get("records") or []
            if isinstance(r, dict)
        ]
        return {"records": records, "notice": result.get("notice")}

    async def chat(request: Request, uid: str, _: dict[str, Any]) -> dict[str, Any]:
        body = await _body(request)
        keys = {"hr": s.dify_hr_app_key, "policy": s.dify_policy_app_key}
        key = keys.get(_text(body.get("app"), 16))
        query = _text(body.get("query"), 2000)
        conversation = _text(body.get("conversation_id"), 64)
        if key is None or not query:
            return {"error": "请输入问题。"}
        if conversation and not CONVERSATION.fullmatch(conversation):
            conversation = ""
        result = await api.chat(key.get_secret_value(), uid, query, conversation)
        if result.get("_conversation_missing"):
            result = await api.chat(key.get_secret_value(), uid, query, "")
        return {
            "answer": str(result.get("answer") or ""),
            "conversation_id": str(result.get("conversation_id") or ""),
        }

    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def index(_: Request) -> Response:
        return FileResponse(STATIC / "index.html")

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with client:
            yield

    routes = [
        Route("/healthz", healthz),
        Route("/", index),
        Route("/auth/login", login),
        Route("/auth/callback", login_callback),
        Route("/auth/logout", logout, methods=["POST"]),
        Route("/api/me", endpoint(me)),
        Route("/api/search", endpoint(search), methods=["POST"]),
        Route("/api/chat", endpoint(chat), methods=["POST"]),
        *[Route(path, endpoint(read)) for path in READS],
        *[Route(path, endpoint(write), methods=["POST"]) for path in WRITES],
        Mount("/static", app=StaticFiles(directory=STATIC)),
    ]
    return _Headers(Starlette(routes=routes, lifespan=lifespan))
