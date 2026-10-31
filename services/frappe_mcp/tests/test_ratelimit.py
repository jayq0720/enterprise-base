from pydantic import SecretStr
from starlette.testclient import TestClient

from frappe_mcp import ratelimit
from frappe_mcp.app import create_app
from frappe_mcp.config import Settings
from frappe_mcp.ratelimit import Limiter

from .conftest import TOKEN, FakeHR

AUTH = {"Authorization": f"Bearer {TOKEN}"}
PORTAL_TOKEN = "p" * 40
INTAKE_TOKEN = "i" * 40


def test_limiter_window() -> None:
    now = [1000.0]
    lim = Limiter(per_minute=2, clock=lambda: now[0])
    assert lim.allow("a") and lim.allow("a")
    assert not lim.allow("a")
    # 别人不受影响
    assert lim.allow("b")
    now[0] += 60
    assert lim.allow("a")


def test_limiter_daily_resets_next_day() -> None:
    day = ["2026-11-02"]
    lim = Limiter(per_minute=0, per_day=2, today=lambda: day[0])
    assert lim.allow("x") and lim.allow("x")
    assert not lim.allow("x")
    day[0] = "2026-11-03"
    assert lim.allow("x")


def test_zero_means_unlimited() -> None:
    lim = Limiter(0, 0)
    assert all(lim.allow("a") for _ in range(1000))


def test_many_keys_do_not_grow_forever() -> None:
    lim = Limiter(per_minute=5)
    for i in range(ratelimit.MAX_KEYS + 10):
        assert lim.allow(f"u{i}")
    assert len(lim._recent) <= ratelimit.MAX_KEYS


def test_session_rate_limited_per_user(settings: Settings) -> None:
    s = settings.model_copy(update={"rate_per_minute": 3})
    with TestClient(create_app(s, FakeHR())) as c:
        got = [
            c.post("/v1/session", json={"user_id": "zhangsan"}, headers=AUTH).json()
            for _ in range(4)
        ]
        assert ["session_token" in g for g in got] == [True, True, True, False]
        assert got[3]["limited"] is True
        assert (
            "一分钟后"
            in c.post("/v1/session", json={"user_id": "zhangsan"}, headers=AUTH).json()["error"]
        )
        assert c.post("/v1/session", json={"user_id": "lisi"}, headers=AUTH).status_code == 200


def test_portal_rate_limited_per_user(settings: Settings) -> None:
    s = settings.model_copy(
        update={"mcp_portal_token": SecretStr(PORTAL_TOKEN), "portal_rate_per_minute": 2}
    )
    headers = {"Authorization": f"Bearer {PORTAL_TOKEN}"}
    with TestClient(create_app(s, FakeHR())) as c:
        codes = [
            c.post("/v1/portal/profile", json={"user_id": "zhangsan"}, headers=headers).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]


def test_intake_rate_limited(settings: Settings) -> None:
    s = settings.model_copy(
        update={"mcp_intake_token": SecretStr(INTAKE_TOKEN), "intake_per_minute": 2}
    )
    headers = {"Authorization": f"Bearer {INTAKE_TOKEN}"}
    hr = FakeHR()
    with TestClient(create_app(s, hr)) as c:
        codes = [
            c.post(
                "/v1/inquiry/intake", json={"email": f"a{i}@b.example"}, headers=headers
            ).status_code
            for i in range(3)
        ]
        assert codes == [200, 200, 429]
    # 被拒的那条没有建询盘
    assert len([x for x in hr.created if x.get("doctype") == "Lead"]) <= 2
