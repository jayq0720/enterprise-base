"""员工前台（X6）：扫码登录 → 只能看和办本人的事 → 请假、两级审批走通 → 跨站和冒充都拒绝。

不连真实企业微信：测试在本机起一个假的企业微信接口（gettoken、auth/getuserinfo），
code 形如 e2e-<userid>；前台的 EB_PORTAL_WECOM_API_BASE 指向它。没配 EB_E2E_PORTAL_URL 时跳过。
"""

import datetime
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from e2e.clients import Frappe
from e2e.settings import E2ESettings
from e2e.world import World, drop_leave

from .conftest import Flow, assert_clean


class _FakeWeCom(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path.endswith("/gettoken"):
            body: dict[str, Any] = {"errcode": 0, "access_token": "e2e-token", "expires_in": 7200}
        elif url.path.endswith("/auth/getuserinfo") and query.get("code", [""])[0].startswith(
            "e2e-"
        ):
            body = {"errcode": 0, "userid": query["code"][0].removeprefix("e2e-")}
        else:
            body = {"errcode": 40029, "errmsg": "invalid code"}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def fake_wecom(settings: E2ESettings) -> Iterator[None]:
    if settings.portal_url is None:
        pytest.skip("未配置 EB_E2E_PORTAL_URL，跳过员工前台测试")
    server = ThreadingHTTPServer(("0.0.0.0", settings.portal_fake_wecom_port), _FakeWeCom)  # noqa: S104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield
    server.shutdown()


class Browser:
    """一个员工的浏览器：自己管会话 Cookie（测试环境是 http，httpx 不会自动带 Secure Cookie）。"""

    def __init__(self, s: E2ESettings) -> None:
        assert s.portal_url is not None
        self.origin = str(s.portal_url).rstrip("/")
        self.http = httpx.Client(base_url=self.origin, timeout=120, follow_redirects=False)
        self.cookies: dict[str, str] = {}

    def _keep(self, r: httpx.Response) -> httpx.Response:
        for header in r.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            value = rest.split(";", 1)[0]
            if "max-age=0" in header.lower() or not value.strip('"'):
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        return r

    def _cookie(self) -> dict[str, str]:
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}

    def login(self, wecom: str) -> str:
        start = self._keep(self.http.get("/auth/login"))
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        back = self.http.get(
            "/auth/callback",
            params={"code": f"e2e-{wecom}", "state": state},
            headers=self._cookie(),
        )
        self._keep(back)
        return str(back.headers["location"])

    def get(self, path: str, **headers: str) -> httpx.Response:
        h = {"X-EB-Portal": "1", **self._cookie(), **headers}
        return self._keep(self.http.get(path, headers=h))

    def post(self, path: str, body: dict[str, Any], **headers: str) -> httpx.Response:
        h = {"X-EB-Portal": "1", "Origin": self.origin, **self._cookie(), **headers}
        return self._keep(self.http.post(path, json=body, headers=h))

    def ok(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self.get(path) if body is None else self.post(path, body)
        assert r.status_code == 200, (path, r.status_code, r.text)
        data: dict[str, Any] = r.json()
        return data


def test_login_only_active_employees(settings: E2ESettings, world: World, fake_wecom: None) -> None:
    nobody = Browser(settings)
    assert nobody.login(f"{world.tag.lower()}-nobody") == "/?login=denied"
    assert nobody.get("/api/me").status_code == 401
    a = Browser(settings)
    assert a.login(world.a.wecom) == "/"
    me = a.ok("/api/me")
    assert me["designation"] == world.a.designation and all(me["features"].values())
    duties = a.ok("/api/duties")
    assert world.a.duties in duties["responsibilities"]
    assert world.b.duties not in str(duties)
    found = a.ok("/api/search", {"query": f"{world.tag} 制度"})
    assert_clean(world, me, duties, found, a.ok("/api/leave/balance"))
    # 页面本身带安全头
    page = a.http.get("/")
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    a.ok("/auth/logout", {})
    assert a.get("/api/me").status_code == 401


def test_leave_and_two_level_approval(
    settings: E2ESettings, frappe: Frappe, world: World, flow: Flow, fake_wecom: None
) -> None:
    world.allocate_leave(frappe, settings.leave_type)
    a, b, c = Browser(settings), Browser(settings), Browser(settings)
    for browser, wecom in ((a, world.a.wecom), (b, world.b.wecom), (c, flow.hr_wecom)):
        assert browser.login(wecom) == "/"
    assert settings.leave_type in a.ok("/api/leave/types")["leave_types"]
    day = datetime.date.today() + datetime.timedelta(days=70)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    draft = a.ok(
        "/api/leave/draft",
        {
            "leave_type": settings.leave_type,
            "from_date": day.isoformat(),
            "to_date": day.isoformat(),
            "reason": f"{world.tag} 前台请假",
        },
    )
    # 别人拿到草稿令牌提交不了
    assert b.ok("/api/leave/confirm", {"draft_token": draft["draft_token"]})["ok"] is False
    done = a.ok("/api/leave/confirm", {"draft_token": draft["draft_token"]})
    assert done["ok"] is True, done
    leave = str(done["id"])

    def drop() -> None:
        if frappe.get("Leave Application", leave)["docstatus"] == 1:
            frappe.as_user(flow.hr_user).apply_workflow(
                "Leave Application", leave, "EB Cancel Leave"
            )
        drop_leave(frappe, leave)

    world.cleanup.append(drop)
    assert leave in str(a.ok("/api/leave/applications"))

    pending = [x["id"] for x in b.ok("/api/approvals")["applications"]]
    assert leave in pending
    approve = {"leave": leave, "action": "approve"}
    # 申请人自己、HR 抢先、冒充 B（请求体里塞 user_id）、跨站请求：都不行，单据不动
    assert a.ok("/api/approvals/decide", {**approve, "user_id": world.b.wecom})["ok"] is False
    assert c.ok("/api/approvals/decide", approve)["ok"] is False
    assert (
        b.post("/api/approvals/decide", approve, Origin="https://evil.example").status_code == 403
    )
    assert frappe.get("Leave Application", leave)["eb_leave_state"] == "EB Leave Manager"

    assert b.ok("/api/approvals/decide", approve)["ok"] is True
    assert frappe.get("Leave Application", leave)["eb_leave_state"] == "EB Leave HR"
    assert b.ok("/api/approvals/decide", approve)["ok"] is False
    assert leave in [x["id"] for x in c.ok("/api/approvals")["applications"]]
    assert c.ok("/api/approvals/decide", approve)["ok"] is True
    doc = frappe.get("Leave Application", leave)
    assert (doc["status"], doc["docstatus"], doc["modified_by"]) == ("Approved", 1, flow.hr_user)
    assert_clean(world, draft, done, pending)


def test_switch_off_hides_and_rejects(
    settings: E2ESettings, frappe: Frappe, world: World, fake_wecom: None
) -> None:
    b = Browser(settings)
    assert b.login(world.b.wecom) == "/"
    frappe.update("Designation", world.b.designation, {"eb_tool_approval": 0})
    try:
        # 前台每 EB_PORTAL_RECHECK_SECONDS 才重新确认一次；接口本身立即拒绝
        out = b.ok("/api/approvals/decide", {"leave": "HR-LAP-NONE", "action": "approve"})
        assert out["ok"] is False and "没有开通" in out["error"]
        assert "没有开通" in b.ok("/api/approvals").get("error", "")
    finally:
        frappe.update("Designation", world.b.designation, {"eb_tool_approval": 1})


def test_hr_assistant_chat_is_own_identity(
    settings: E2ESettings, world: World, fake_wecom: None
) -> None:
    a = Browser(settings)
    assert a.login(world.a.wecom) == "/"
    if not a.ok("/api/me")["chat"]["hr"]:
        pytest.skip("前台没配 HR 助手应用 Key，跳过对话")
    reply = a.ok("/api/chat", {"app": "hr", "query": "我的岗位职责是什么", "user": world.b.wecom})
    assert world.a.duties in reply["answer"] and world.b.duties not in reply["answer"]
    assert_clean(world, reply)
