import json

import httpx
import pytest

from portal.backends import Backends, Unavailable
from portal.config import Settings


def make(settings: Settings, handler: httpx.MockTransport) -> Backends:
    return Backends(settings, httpx.AsyncClient(transport=handler))


async def test_wecom_code_to_userid_caches_token(settings: Settings) -> None:
    seen: list[str] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path.endswith("/gettoken"):
            assert req.url.params["corpid"] == "synthetic-corp"
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200}
            )
        if req.url.params["code"] == "good":
            return httpx.Response(200, json={"errcode": 0, "userid": "zhangsan"})
        return httpx.Response(200, json={"errcode": 40029, "errmsg": "invalid code"})

    api = make(settings, httpx.MockTransport(handle))
    assert await api.wecom_userid("good") == "zhangsan"
    assert await api.wecom_userid("bad") is None
    # access_token 缓存，不每次都换
    assert seen.count("/cgi-bin/gettoken") == 1


async def test_wecom_down_returns_none(settings: Settings) -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await make(settings, httpx.MockTransport(handle)).wecom_userid("good") is None


async def test_x4_x3_dify_requests(settings: Settings) -> None:
    sent: list[tuple[str, str, dict[str, object]]] = []

    def handle(req: httpx.Request) -> httpx.Response:
        sent.append((str(req.url), req.headers["authorization"], json.loads(req.content)))
        if "chat-messages" in str(req.url) and json.loads(req.content)["conversation_id"]:
            return httpx.Response(
                404, json={"code": "not_found", "message": "Conversation Not Exists."}
            )
        return httpx.Response(200, json={"ok": True})

    api = make(settings, httpx.MockTransport(handle))
    await api.x4("decide", "zhangsan", leave="HR-LAP-1", action="approve")
    await api.search("zhangsan", "年假")
    await api.chat("app-key", "zhangsan", "你好", "")
    missing = await api.chat("app-key", "zhangsan", "你好", "0b8d6c1e-1111-4222-8333-444455556666")
    assert missing == {"_conversation_missing": True}
    assert sent[0] == (
        "http://frappe-mcp:8080/v1/portal/decide",
        "Bearer " + "p" * 40,
        {"leave": "HR-LAP-1", "action": "approve", "user_id": "zhangsan"},
    )
    assert sent[1][0] == "http://kb-gateway:8080/v1/search" and sent[1][2]["user_id"] == "zhangsan"
    assert sent[2][0] == "http://dify-api:5001/v1/chat-messages"
    assert sent[2][1] == "Bearer app-key" and sent[2][2]["user"] == "zhangsan"


async def test_backend_errors_raise_unavailable(settings: Settings) -> None:
    api = make(settings, httpx.MockTransport(lambda req: httpx.Response(500, text="boom")))
    with pytest.raises(Unavailable):
        await api.x4("profile", "zhangsan")
