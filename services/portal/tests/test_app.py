import logging
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from portal import session
from portal.app import COOKIE, LOGIN_COOKIE, create_app
from portal.config import Settings

from .conftest import HR_KEY, ORIGIN, SECRET, FakeBackends

API = {"X-EB-Portal": "1", "Origin": ORIGIN}


def client(settings: Settings, fake: FakeBackends) -> TestClient:
    return TestClient(create_app(settings, fake), base_url=ORIGIN, follow_redirects=False)


def login(c: TestClient, userid: str = "zhangsan", ua: str = "Mozilla/5.0") -> Any:
    start = c.get("/auth/login", headers={"User-Agent": ua})
    assert start.status_code == 303
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    return c.get("/auth/callback", params={"code": f"code-{userid}", "state": state})


def test_login_redirects_to_wecom(settings: Settings) -> None:
    with client(settings, FakeBackends()) as c:
        resp = c.get("/auth/login")
        url = urlsplit(resp.headers["location"])
        query = parse_qs(url.query)
        assert url.netloc == "login.work.weixin.qq.com"
        assert query["appid"] == ["synthetic-corp"] and query["agentid"] == ["1000002"]
        assert query["redirect_uri"] == [ORIGIN + "/auth/callback"]
        cookie = resp.headers["set-cookie"]
        assert cookie.startswith(LOGIN_COOKIE) and "HttpOnly" in cookie and "Secure" in cookie
        assert "synthetic-app-secret" not in resp.headers["location"]
        inside = c.get("/auth/login", headers={"User-Agent": "Mozilla/5.0 wxwork/4.1"})
        assert inside.headers["location"].startswith("https://open.weixin.qq.com/")
        assert inside.headers["location"].endswith("#wechat_redirect")


def test_login_success_sets_strict_session(settings: Settings) -> None:
    with client(settings, FakeBackends()) as c:
        resp = login(c)
        assert resp.status_code == 303 and resp.headers["location"] == "/"
        cookies = resp.headers.get_list("set-cookie")
        sess = next(v for v in cookies if v.startswith(COOKIE + "="))
        assert "HttpOnly" in sess and "Secure" in sess and "SameSite=strict" in sess
        me = c.get("/api/me", headers=API).json()
        assert me["employee_name"] == "张三" and me["chat"] == {
            "hr": True,
            "policy": False,
            "marketing": False,
        }
        assert HR_KEY not in str(me)


def test_callback_rejects_forged_or_replayed_state(settings: Settings) -> None:
    fake = FakeBackends()
    with client(settings, fake) as c:
        # 没走 /auth/login（没有 nonce Cookie）
        state = session.sign(SECRET.encode(), session.STATE, {"n": "x"}, 300)
        bad = c.get("/auth/callback", params={"code": "code-zhangsan", "state": state})
        assert bad.headers["location"] == "/?login=failed"
        start = c.get("/auth/login")
        real = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        for params in (
            {"code": "code-zhangsan", "state": real + "x"},
            {"code": "code-zhangsan", "state": state},
            {"code": "code-zhangsan"},
            {"code": "code-zhangsan", "state": real, "extra": "1"},
            {"code": "wrong", "state": real},
        ):
            resp = c.get("/auth/callback", params=params)
            assert resp.headers["location"] == "/?login=failed"
            assert COOKIE + "=" not in str(resp.headers.get_list("set-cookie"))
        # 另一个浏览器（不同 nonce）拿到这个 state 也用不了
        other = client(settings, fake)
        other.cookies.set(LOGIN_COOKIE, "A" * 43)
        resp = other.get("/auth/callback", params={"code": "code-zhangsan", "state": real})
        assert resp.headers["location"] == "/?login=failed"


def test_non_employee_cannot_log_in(settings: Settings) -> None:
    with client(settings, FakeBackends()) as c:
        resp = login(c, "ghost")
        assert resp.headers["location"] == "/?login=denied"
        assert c.get("/api/me", headers=API).status_code == 401


def test_api_requires_session_and_same_origin(settings: Settings) -> None:
    fake = FakeBackends()
    with client(settings, fake) as c:
        assert c.get("/api/me", headers=API).status_code == 401
        login(c)
        assert c.get("/api/me").status_code == 403
        post = {"X-EB-Portal": "1"}
        body = {"leave": "HR-LAP-1", "action": "approve"}
        assert c.post("/api/approvals/decide", json=body, headers=post).status_code == 403
        evil = {**post, "Origin": "https://evil.test"}
        assert c.post("/api/approvals/decide", json=body, headers=evil).status_code == 403
        assert not [x for x in fake.x4_calls if x[0] == "decide"]
    with client(settings, fake) as c:
        # 伪造、过期、用途不对的 Cookie
        for token in (
            session.sign(b"z" * 40, session.LOGIN, {"uid": "lisi"}, 300),
            session.sign(SECRET.encode(), session.LOGIN, {"uid": "lisi"}, -10),
            session.sign(SECRET.encode(), session.STATE, {"uid": "lisi"}, 300),
        ):
            c.cookies.set(COOKIE, token)
            assert c.get("/api/me", headers=API).status_code == 401


def test_identity_only_from_session(settings: Settings) -> None:
    fake = FakeBackends()
    with client(settings, fake) as c:
        login(c)
        body = {"leave": "HR-LAP-1", "action": "approve", "user_id": "lisi", "user": "lisi"}
        c.post("/api/approvals/decide", json=body, headers=API)
        c.get("/api/leave/balance?user_id=lisi", headers=API)
        c.post("/api/search", json={"query": "年假", "user_id": "lisi"}, headers=API)
        c.post("/api/chat", json={"app": "hr", "query": "hi", "user": "lisi"}, headers=API)
        calls = [x for x in fake.x4_calls if x[0] != "profile"]
        assert calls == [
            ("decide", "zhangsan", {"leave": "HR-LAP-1", "action": "approve"}),
            ("leave_balance", "zhangsan", {}),
        ]
        assert fake.searches == [("zhangsan", "年假")]
        assert fake.chats[0][1] == "zhangsan"


def test_left_employee_logged_out(settings: Settings) -> None:
    fake = FakeBackends()
    with client(settings.model_copy(update={"recheck_seconds": 0}), fake) as c:
        login(c)
        assert c.get("/api/me", headers=API).status_code == 200
        del fake.employees["zhangsan"]
        resp = c.get("/api/leave/balance", headers=API)
        assert resp.status_code == 401
        assert not [x for x in fake.x4_calls if x[0] == "leave_balance"]
        fake.down = True
        assert c.get("/api/me", headers=API).status_code == 401


def test_backend_down_is_503(settings: Settings) -> None:
    fake = FakeBackends()
    with client(settings.model_copy(update={"recheck_seconds": 0}), fake) as c:
        login(c)
        fake.down = True
        resp = c.get("/api/duties", headers=API)
        assert resp.status_code == 503 and "暂时不可用" in resp.json()["error"]


def test_chat_and_search(settings: Settings) -> None:
    fake = FakeBackends()
    with client(settings, fake) as c:
        login(c)
        r = c.post("/api/chat", json={"app": "hr", "query": "年假"}, headers=API).json()
        assert r == {
            "answer": "zhangsan 你好",
            "conversation_id": "0b8d6c1e-1111-4222-8333-444455556666",
        }
        assert fake.chats[-1][0] == HR_KEY
        # 没配 Key 的应用、格式不对的会话 ID
        assert (
            "error" in c.post("/api/chat", json={"app": "policy", "query": "x"}, headers=API).json()
        )
        bad = {"app": "hr", "query": "x", "conversation_id": "../../x"}
        c.post("/api/chat", json=bad, headers=API)
        assert fake.chats[-1][3] == ""
        fake.missing_conversation = True
        old = {"app": "hr", "query": "x", "conversation_id": "0b8d6c1e-1111-4222-8333-444455550000"}
        assert c.post("/api/chat", json=old, headers=API).json()["answer"]
        assert fake.chats[-1][3] == ""
        found = c.post("/api/search", json={"query": "年假"}, headers=API).json()
        assert found["records"] == [
            {"title": "年假规定", "content": "满一年 5 天", "space": "全员制度"}
        ]


def test_pages_and_security_headers(settings: Settings) -> None:
    with client(settings, FakeBackends()) as c:
        page = c.get("/")
        assert page.status_code == 200 and "app.js" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["cache-control"] == "no-store"
        assert c.get("/static/app.js").status_code == 200
        assert c.get("/healthz").json() == {"status": "ok"}


def test_logout(settings: Settings) -> None:
    with client(settings, FakeBackends()) as c:
        login(c)
        assert c.post("/auth/logout", headers={"X-EB-Portal": "1"}).status_code == 403
        assert c.post("/auth/logout", headers=API).status_code == 200
        assert c.get("/api/me", headers=API).status_code == 401


def test_logs_have_no_identity(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeBackends()
    with client(settings, fake) as c:
        login(c)
        c.post("/api/chat", json={"app": "hr", "query": "我的身份证号"}, headers=API)
    text = caplog.text
    for value in ("zhangsan", "code-zhangsan", "身份证", HR_KEY, "synthetic-app-secret"):
        assert value not in text


def test_blank_dify_key_means_chat_off(settings: Settings) -> None:
    data = {**settings.model_dump(), "dify_hr_app_key": "", "dify_policy_app_key": " "}
    blank = Settings(**data)
    assert blank.dify_hr_app_key is None and blank.dify_policy_app_key is None


def test_public_url_must_be_https(settings: Settings) -> None:
    data = settings.model_dump()
    with pytest.raises(ValueError):
        Settings(**{**data, "public_url": "http://portal.example.com"})
    with pytest.raises(ValueError):
        Settings(**{**data, "public_url": "https://portal.example.com/app"})
    with pytest.raises(ValueError):
        Settings(**{**data, "public_url": "http://localhost.example.com"})
    Settings(**{**data, "public_url": "http://portal.localhost"})


def test_inquiry_pages_identity_from_session(settings: Settings) -> None:
    fake = FakeBackends()
    marketing = settings.model_copy(
        update={"dify_marketing_app_key": SecretStr("app-mk-key-synthetic")}
    )
    with client(marketing, fake) as c:
        login(c)
        assert c.get("/api/me", headers=API).json()["chat"]["marketing"] is True
        c.get("/api/inquiries?user_id=lisi", headers=API)
        c.get("/api/inquiries?closed=1", headers=API)
        detail = {"inquiry_id": "CRM-LEAD-1", "user_id": "lisi", "contact": "1"}
        c.post("/api/inquiries/detail", json=detail, headers=API)
        follow = {"inquiry_id": "CRM-LEAD-1", "status": "已回复", "note": "x", "userid": "lisi"}
        c.post("/api/inquiries/follow-up", json=follow, headers=API)
        c.post("/api/inquiries/stats", json={"days": "7"}, headers=API)
        c.post("/api/chat", json={"app": "marketing", "query": "我的询盘"}, headers=API)
        calls = [x for x in fake.x4_calls if x[0] != "profile"]
        # 身份只来自登录会话；浏览器传来的 user_id、contact 等多余字段一律不转给 X4
        assert calls == [
            ("my_inquiries", "zhangsan", {}),
            ("my_inquiries", "zhangsan", {"include_closed": "1"}),
            ("inquiry_detail", "zhangsan", {"inquiry_id": "CRM-LEAD-1"}),
            (
                "follow_up_inquiry",
                "zhangsan",
                {"inquiry_id": "CRM-LEAD-1", "status": "已回复", "note": "x"},
            ),
            ("inquiry_stats", "zhangsan", {"days": "7"}),
        ]
        assert fake.chats[-1][:2] == ("app-mk-key-synthetic", "zhangsan")
        # 写接口同样要同源
        bad = {"X-EB-Portal": "1", "Origin": "https://evil.test"}
        assert c.post("/api/inquiries/follow-up", json=follow, headers=bad).status_code == 403
    with client(settings, FakeBackends()) as c:
        login(c)
        assert c.get("/api/me", headers=API).json()["chat"]["marketing"] is False


def test_intake_passthrough_without_login(settings: Settings) -> None:
    fake = FakeBackends()
    good = {"Authorization": "Bearer " + "i" * 40}
    with client(settings, fake) as c:
        # 不用登录、不带页面头也能进件；Token 原样交给 X4 判断
        ok = c.post("/inquiry/intake", json={"email": "a@b.example"}, headers=good)
        assert ok.status_code == 200 and ok.json()["id"] == "CRM-LEAD-2"
        assert fake.intakes[-1] == (good["Authorization"], b'{"email":"a@b.example"}')
        bad = c.post("/inquiry/intake", json={}, headers={"Authorization": "Bearer wrong"})
        assert bad.status_code == 401
        before = len(fake.intakes)
        assert c.post("/inquiry/intake", json={}).status_code == 401
        big = c.post("/inquiry/intake", content=b"x" * 40000, headers=good)
        assert big.status_code == 413 and len(fake.intakes) == before
        assert c.get("/inquiry/intake").status_code == 405
        fake.down = True
        assert c.post("/inquiry/intake", json={}, headers=good).status_code == 503
