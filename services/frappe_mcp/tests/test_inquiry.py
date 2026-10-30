"""询盘工具：只查本人名下、岗位开关、模型拿不到客户联系方式、进件 Token 独立。"""

from typing import Any

import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from frappe_mcp import session
from frappe_mcp.app import create_app
from frappe_mcp.config import Settings
from frappe_mcp.tools import Tools

from .conftest import SECRET, TOKEN, FakeHR

INTAKE_TOKEN = "i" * 40
PORTAL_TOKEN = "p" * 40


def tok(uid: str) -> str:
    return session.issue(SECRET.encode(), uid, 300)


@pytest.fixture
def hr() -> FakeHR:
    return FakeHR()


@pytest.fixture
def tools(settings: Settings, hr: FakeHR) -> Tools:
    return Tools(settings, hr)


async def test_my_inquiries_identity_from_token(tools: Tools, hr: FakeHR) -> None:
    result = await tools.my_inquiries(tok("zhangsan"))
    assert result["ok"]
    item = result["inquiries"][0]
    assert item["id"] == "CRM-LEAD-1" and item["status"] == "新询盘" and item["grade"] == "A"
    assert item["received_at"] == "2026-10-30 10:00"
    # 身份只来自令牌：Frappe 收到的 userid 就是令牌里的人
    assert hr.inquiry_calls[-1] == ("my_inquiries", {"userid": "zhangsan", "scope": "open"})
    await tools.my_inquiries(tok("zhangsan"), include_closed=True)
    assert hr.inquiry_calls[-1][1]["scope"] == "all"


async def test_detail_hides_contact_for_model(tools: Tools, hr: FakeHR) -> None:
    result = await tools.inquiry_detail(tok("zhangsan"), " CRM-LEAD-1 ")
    assert result["ok"] and result["inquiry"]["requirement"] == "Need 500 pcs"
    assert result["inquiry"]["source"]["ai_engine"] == "ChatGPT"
    assert "contact" not in result["inquiry"] and "alice@acme.example" not in str(result)
    assert hr.inquiry_calls[-1][1]["contact"] == 0
    full = await tools.inquiry_detail(tok("zhangsan"), "CRM-LEAD-1", contact=True)
    assert full["inquiry"]["contact"] == {"email": "alice@acme.example", "phone": "+49 1"}


async def test_others_inquiry_forbidden(tools: Tools) -> None:
    for call in (
        tools.inquiry_detail(tok("lisi"), "CRM-LEAD-1"),
        tools.follow_up_inquiry(tok("lisi"), "CRM-LEAD-1", "已回复"),
        tools.inquiry_detail(tok("zhangsan"), "CRM-LEAD-9"),
    ):
        result = await call
        assert result == {"ok": False, "error": "这条询盘不在你名下，或者已经不存在。"}


async def test_follow_up_status_mapping(tools: Tools, hr: FakeHR) -> None:
    result = await tools.follow_up_inquiry(tok("zhangsan"), "CRM-LEAD-1", "已回复", "发了报价")
    assert result == {"ok": True, "message": "已更新询盘 CRM-LEAD-1，当前状态：已回复。"}
    assert hr.inquiry_calls[-1][1] == {
        "userid": "zhangsan",
        "lead": "CRM-LEAD-1",
        "status": "Replied",
        "note": "发了报价",
    }
    bad = await tools.follow_up_inquiry(tok("zhangsan"), "CRM-LEAD-1", "已成交")
    assert not bad["ok"] and "跟进中" in bad["error"]
    assert hr.inquiry_calls[-1][0] == "follow_up" and hr.inquiry_calls[-1][1]["status"] == "Replied"


async def test_switches(tools: Tools, hr: FakeHR) -> None:
    hr.switches["出纳"]["eb_tool_inquiry"] = False
    result = await tools.my_inquiries(tok("zhangsan"))
    assert "处理询盘" in result["error"]
    hr.switches["出纳"]["eb_tool_inquiry_stats"] = False
    assert "查询盘统计" in (await tools.inquiry_stats(tok("zhangsan")))["error"]
    stats = await tools.inquiry_stats(tok("lisi"), 9999)
    assert stats["ok"] and stats["days"] == 366 and stats["by_status"] == {"新询盘": 1}
    # 没有岗位、不是员工：一律拒绝，Frappe 都不调
    before = len(hr.inquiry_calls)
    assert "岗位" in (await tools.my_inquiries(tok("wangwu")))["error"]
    assert "未找到" in (await tools.my_inquiries(tok("nobody")))["error"]
    assert len(hr.inquiry_calls) == before


async def test_frappe_down(tools: Tools, hr: FakeHR) -> None:
    hr.down = True
    assert (await tools.intake({"email": "a@b.example"}))["ok"] is False


def test_intake_endpoint_own_token(settings: Settings, hr: FakeHR) -> None:
    with TestClient(create_app(settings, hr)) as c:
        # 没配进件 Token：一律 401，Dify 的 Token 也不行
        for headers in ({}, {"Authorization": f"Bearer {TOKEN}"}):
            assert c.post("/v1/inquiry/intake", json={}, headers=headers).status_code == 401
    s = settings.model_copy(
        update={
            "mcp_intake_token": SecretStr(INTAKE_TOKEN),
            "mcp_portal_token": SecretStr(PORTAL_TOKEN),
        }
    )
    intake = {"Authorization": f"Bearer {INTAKE_TOKEN}"}
    with TestClient(create_app(s, hr)) as c:
        ok = c.post("/v1/inquiry/intake", json={"email": "a@b.example"}, headers=intake)
        assert ok.status_code == 200
        assert ok.json() == {"ok": True, "id": "CRM-LEAD-2", "duplicate": False, "grade": "B"}
        bad = c.post("/v1/inquiry/intake", json={"name": "x"}, headers=intake)
        assert bad.status_code == 422 and "邮箱或电话" in bad.json()["error"]
        assert c.post("/v1/inquiry/intake", content=b"[]", headers=intake).status_code == 422
        big = b'{"requirement": "' + b"x" * 40000 + b'"}'
        assert c.post("/v1/inquiry/intake", content=big, headers=intake).status_code == 422
        # 进件 Token 进不了其他接口，其他 Token 也进不了进件
        assert c.post("/v1/session", json={"user_id": "x"}, headers=intake).status_code == 401
        assert (
            c.post("/v1/portal/profile", json={"user_id": "x"}, headers=intake).status_code == 401
        )
        for other in (TOKEN, PORTAL_TOKEN):
            headers = {"Authorization": f"Bearer {other}"}
            assert c.post("/v1/inquiry/intake", json={}, headers=headers).status_code == 401


def test_portal_inquiry_calls(settings: Settings, hr: FakeHR) -> None:
    s = settings.model_copy(update={"mcp_portal_token": SecretStr(PORTAL_TOKEN)})
    headers = {"Authorization": f"Bearer {PORTAL_TOKEN}"}

    def call(c: TestClient, name: str, **body: Any) -> dict[str, Any]:
        resp = c.post(f"/v1/portal/{name}", json=body, headers=headers)
        assert resp.status_code == 200, resp.text
        data: dict[str, Any] = resp.json()
        return data

    with TestClient(create_app(s, hr)) as c:
        me = call(c, "profile", user_id="zhangsan")
        assert me["features"]["inquiry"] and me["features"]["inquiry_stats"]
        assert call(c, "my_inquiries", user_id="zhangsan")["inquiries"][0]["id"] == "CRM-LEAD-1"
        detail = call(c, "inquiry_detail", user_id="zhangsan", inquiry_id="CRM-LEAD-1")
        # 前台是负责人本人，带联系方式
        assert detail["inquiry"]["contact"]["email"] == "alice@acme.example"
        assert not call(c, "inquiry_detail", user_id="lisi", inquiry_id="CRM-LEAD-1")["ok"]
        done = call(
            c, "follow_up_inquiry", user_id="zhangsan", inquiry_id="CRM-LEAD-1", status="有意向"
        )
        assert done["ok"]
        assert call(c, "inquiry_stats", user_id="lisi", days=7)["days"] == 7
