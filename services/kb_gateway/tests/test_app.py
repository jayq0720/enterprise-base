from fastapi.testclient import TestClient

from kb_gateway.app import create_app
from kb_gateway.config import Settings

from .conftest import SALES, TOKEN, FakeDirectory, FakeKnowledge, rec


def client(settings: Settings) -> TestClient:
    app = create_app(
        settings,
        FakeDirectory({"sales": SALES}),
        FakeKnowledge({"ds-policy": [rec("考勤制度", 0.9, ",all,")]}),
    )
    return TestClient(app)


def test_requires_token(settings: Settings) -> None:
    with client(settings) as c:
        body = {"user_id": "sales", "query": "请假"}
        assert c.post("/v1/search", json=body).status_code == 401
        bad = {"Authorization": "Bearer wrong"}
        assert c.post("/v1/search", json=body, headers=bad).status_code == 401


def test_search_ok(settings: Settings) -> None:
    with client(settings) as c:
        r = c.post(
            "/v1/search",
            json={"user_id": "sales", "query": "请假"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert r.status_code == 200
        assert r.json()["records"][0]["title"] == "考勤制度"


def test_healthz(settings: Settings) -> None:
    with client(settings) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
