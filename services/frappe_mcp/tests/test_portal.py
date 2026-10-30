"""员工前台接口：只认前台 Token，身份只取请求里的 user_id，按岗位开关拒绝。"""

from typing import Any

import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from frappe_mcp.app import create_app
from frappe_mcp.config import Settings

from .conftest import TOKEN, FakeHR

PORTAL_TOKEN = "p" * 40
PORTAL = {"Authorization": f"Bearer {PORTAL_TOKEN}"}
DIFY = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def portal_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"mcp_portal_token": SecretStr(PORTAL_TOKEN)})


def call(c: TestClient, name: str, **body: Any) -> dict[str, Any]:
    resp = c.post(f"/v1/portal/{name}", json=body, headers=PORTAL)
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()
    return data


def test_tokens_not_interchangeable(settings: Settings, portal_settings: Settings) -> None:
    with TestClient(create_app(settings, FakeHR())) as c:
        # 没配前台 Token：前台接口全部 401，Dify 的 Token 也进不去
        for headers in (PORTAL, DIFY, {}):
            resp = c.post("/v1/portal/profile", json={"user_id": "zhangsan"}, headers=headers)
            assert resp.status_code == 401
    with TestClient(create_app(portal_settings, FakeHR())) as c:
        assert c.post("/v1/portal/profile", json={"user_id": "x"}, headers=DIFY).status_code == 401
        assert c.post("/v1/session", json={"user_id": "x"}, headers=PORTAL).status_code == 401
        assert c.post("/mcp/", json={}, headers=PORTAL).status_code == 401
        assert c.post("/v1/portal/profile", json={}, headers=PORTAL).status_code == 422
        assert c.post("/v1/portal/nope", json={"user_id": "x"}, headers=PORTAL).status_code == 404


def test_profile(portal_settings: Settings) -> None:
    hr = FakeHR()
    hr.switches["出纳"]["eb_tool_approval"] = False
    with TestClient(create_app(portal_settings, hr)) as c:
        me = call(c, "profile", user_id="zhangsan")
        assert me["ok"] and me["employee_name"] == "张三" and me["designation"] == "出纳"
        assert me["features"] == {
            "leave_query": True,
            "leave_apply": True,
            "approval": False,
            "duties": True,
        }
        assert "user_id" not in me and "zs@example.com" not in str(me)
        # 没有岗位：能登录但所有功能关闭，并提示补岗位
        nobody = call(c, "profile", user_id="wangwu")
        assert nobody["ok"] and not any(nobody["features"].values()) and nobody["notice"]
        assert call(c, "profile", user_id="ghost")["ok"] is False


def test_tools_use_request_identity_only(portal_settings: Settings) -> None:
    hr = FakeHR()
    with TestClient(create_app(portal_settings, hr)) as c:
        bal = call(c, "leave_balance", user_id="zhangsan", employee="E2", user="lisi")
        assert bal["employee_name"] == "张三" and hr.calls[-1] == ("balance", "E1")
        assert call(c, "my_responsibilities", user_id="wangwu")["error"]
        assert call(c, "leave_balance", user_id="ghost")["error"]
        assert call(c, "leave_types", user_id="zhangsan") == {"leave_types": ["年假", "病假"]}
        pending = call(c, "pending_my_approval", user_id="lisi")
        assert pending["applications"][0]["id"] == "HR-LAP-1"


def test_draft_and_confirm_bound_to_user(portal_settings: Settings) -> None:
    hr = FakeHR()
    with TestClient(create_app(portal_settings, hr)) as c:
        draft = call(
            c,
            "draft_leave",
            user_id="zhangsan",
            leave_type="年假",
            from_date="2026-11-02",
            to_date="2026-11-03",
            reason="家事",
        )
        token = draft["draft_token"]
        assert hr.created == []
        assert call(c, "confirm_leave", user_id="lisi", draft_token=token)["ok"] is False
        assert call(c, "confirm_leave", user_id="zhangsan", draft_token="x.y")["ok"] is False
        assert hr.created == []
        assert call(c, "confirm_leave", user_id="zhangsan", draft_token=token)["ok"] is True
        assert hr.created[0]["employee"] == "E1"


def test_decide(portal_settings: Settings) -> None:
    hr = FakeHR()
    with TestClient(create_app(portal_settings, hr)) as c:
        ok = call(c, "decide", user_id="lisi", leave="HR-LAP-1", action="approve")
        assert ok["ok"] is True and hr.decided == [("lisi", "HR-LAP-1", "approve")]
        # 不是本人环节的单：Frappe 拒绝，转成员工能看懂的话
        denied = call(c, "decide", user_id="zhangsan", leave="HR-LAP-1", action="approve")
        assert denied["ok"] is False and "待审批" in denied["error"]
        bad = call(c, "decide", user_id="lisi", leave="HR-LAP-1", action="submit")
        assert bad["ok"] is False and len(hr.decided) == 2
        # 没有岗位、岗位关掉审批：不调 Frappe
        nobody = call(c, "decide", user_id="wangwu", leave="HR-LAP-1", action="approve")
        assert nobody["ok"] is False
        hr.switches["主管"]["eb_tool_approval"] = False
        off = call(c, "decide", user_id="lisi", leave="HR-LAP-1", action="return")
        assert off["ok"] is False and "没有开通" in off["error"]
        assert len(hr.decided) == 2
