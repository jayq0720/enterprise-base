import asyncio
import json

from fastapi.testclient import TestClient

from policy_sync.app import SIGNATURE_HEADER, create_app, sign
from policy_sync.config import Settings
from tests.conftest import SECRET, TOKEN, FakeKnowledge, FakeWiki


def client(settings: Settings) -> tuple[TestClient, FakeWiki, FakeKnowledge]:
    wiki, kb = FakeWiki(), FakeKnowledge()
    return TestClient(create_app(settings, wiki, kb)), wiki, kb


def post_hook(c: TestClient, payload: dict[str, str], secret: str = SECRET) -> int:
    body = json.dumps(payload).encode()
    headers = {SIGNATURE_HEADER: sign(secret.encode(), body), "Content-Type": "application/json"}
    resp = c.post("/v1/hooks/wiki", content=body, headers=headers)
    return int(resp.status_code)


def drain(c: TestClient) -> None:
    async def wait() -> None:
        for _ in range(50):
            await asyncio.sleep(0)

    c.portal.call(wait)  # type: ignore[union-attr]


def test_signed_hook_triggers_sync(settings: Settings) -> None:
    c, wiki, kb = client(settings)
    wiki.put("WD1", "正文")
    with c:
        assert post_hook(c, {"doctype": "Wiki Document", "name": "WD1"}) == 202
        drain(c)
    assert len(kb.copies("WD1")) == 1


def test_bad_or_missing_signature_rejected(settings: Settings) -> None:
    c, wiki, kb = client(settings)
    wiki.put("WD1", "正文")
    with c:
        assert post_hook(c, {"name": "WD1"}, secret="x" * 40) == 401
        assert c.post("/v1/hooks/wiki", json={"name": "WD1"}).status_code == 401
        drain(c)
    assert kb.copies("WD1") == []


def test_other_doctype_ignored(settings: Settings) -> None:
    c, _, kb = client(settings)
    with c:
        assert post_hook(c, {"doctype": "Employee", "name": "E1"}) == 202
    assert kb.created == 0


def test_reconcile_requires_token(settings: Settings) -> None:
    c, wiki, kb = client(settings)
    wiki.put("WD1", "正文")
    with c:
        assert c.post("/v1/reconcile").status_code == 401
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert c.post("/v1/reconcile", headers=auth).status_code == 202
        drain(c)
    assert len(kb.copies("WD1")) == 1
