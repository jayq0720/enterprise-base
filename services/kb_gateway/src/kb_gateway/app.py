"""HTTP 入口。"""

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request

from kb_gateway.config import Settings
from kb_gateway.dify import DifyKnowledge, Knowledge
from kb_gateway.directory import Directory, FrappeDirectory
from kb_gateway.models import SearchRequest, SearchResponse
from kb_gateway.ratelimit import Limiter
from kb_gateway.service import SearchService


def create_app(
    settings: Settings | None = None,
    directory: Directory | None = None,
    knowledge: Knowledge | None = None,
) -> FastAPI:
    s = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            app.state.service = SearchService(
                s,
                directory or FrappeDirectory(s, client),
                knowledge or DifyKnowledge(s, client),
            )
            yield

    app = FastAPI(title="kb-gateway", version="0.1.0", lifespan=lifespan)
    expected = s.kb_service_token.get_secret_value().encode()
    limiter = Limiter(s.rate_per_minute)

    def check_token(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/search", dependencies=[Depends(check_token)])
    async def search(req: SearchRequest, request: Request) -> SearchResponse:
        if not limiter.allow(req.user_id):
            raise HTTPException(status_code=429, detail="消息太多了，请一分钟后再试。")
        service: SearchService = request.app.state.service
        return await service.search(req)

    return app
