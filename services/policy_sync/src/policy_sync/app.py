"""HTTP 入口。

- `POST /v1/hooks/wiki`：Frappe Webhook（Wiki Document 保存、删除时推送），
  校验签名后立刻返回，后台同步
- `POST /v1/reconcile`：带服务 Token 手动触发全量对账
"""

import asyncio
import base64
import hashlib
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

from policy_sync.config import Settings
from policy_sync.dify import DifyKnowledge, Knowledge
from policy_sync.frappe import FrappeWiki, Wiki
from policy_sync.models import WebhookEvent
from policy_sync.sync import Syncer, with_retry

SIGNATURE_HEADER = "X-Frappe-Webhook-Signature"


def sign(secret: bytes, body: bytes) -> str:
    """和 Frappe 一致：base64(HMAC-SHA256(secret, 请求体))。"""
    return base64.b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode()


def create_app(
    settings: Settings | None = None,
    wiki: Wiki | None = None,
    knowledge: Knowledge | None = None,
) -> FastAPI:
    s = settings or Settings()
    log = logging.getLogger("policy_sync")
    if not log.handlers:
        # 同步结果和失败要留记录；日志里只有页面编号和错误类型，不写正文
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    tasks: set[asyncio.Task[object]] = set()

    def spawn(label: str, fn: Callable[[], Awaitable[object]]) -> None:
        task: asyncio.Task[object] = asyncio.create_task(with_retry(s, label, fn))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def periodic(syncer: Syncer) -> None:
        while True:
            await asyncio.sleep(s.reconcile_interval_seconds)
            await with_retry(s, "定时对账", syncer.reconcile)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            syncer = Syncer(s, wiki or FrappeWiki(s, client), knowledge or DifyKnowledge(s, client))
            app.state.syncer = syncer
            ticker = asyncio.create_task(periodic(syncer)) if s.reconcile_interval_seconds else None
            yield
            for t in [*tasks, *([ticker] if ticker else [])]:
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t

    app = FastAPI(title="policy-sync", version="0.1.0", lifespan=lifespan)
    webhook_secret = s.sync_webhook_secret.get_secret_value().encode()
    service_token = s.sync_service_token.get_secret_value().encode()

    def check_token(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), service_token):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/hooks/wiki", status_code=202)
    async def wiki_hook(
        request: Request,
        signature: Annotated[str | None, Header(alias=SIGNATURE_HEADER)] = None,
    ) -> dict[str, str]:
        body = await request.body()
        if not signature or not hmac.compare_digest(signature, sign(webhook_secret, body)):
            raise HTTPException(status_code=401, detail="bad signature")
        try:
            event = WebhookEvent.model_validate_json(body)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail="需要 name 字段") from e
        if event.doctype != "Wiki Document":
            return {"status": "ignored"}
        syncer: Syncer = request.app.state.syncer
        spawn(f"页面 {event.name}", lambda: syncer.sync_page(event.name))
        return {"status": "accepted"}

    @app.post("/v1/reconcile", status_code=202, dependencies=[Depends(check_token)])
    async def reconcile(request: Request) -> dict[str, str]:
        syncer: Syncer = request.app.state.syncer
        spawn("手动对账", syncer.reconcile)
        return {"status": "accepted"}

    return app
