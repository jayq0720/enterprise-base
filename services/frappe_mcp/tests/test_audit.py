"""审计：每次工具调用记一条，入口按路由区分，不记正文和令牌，写 Frappe 失败不影响操作。"""

import datetime
import json
import logging

import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from frappe_mcp import session
from frappe_mcp.app import create_app
from frappe_mcp.config import Settings
from frappe_mcp.tools import Tools

from .conftest import SECRET, TOKEN, FakeHR

PORTAL_TOKEN = "p" * 40


def make(settings: Settings) -> tuple[Tools, FakeHR]:
    hr = FakeHR()
    return Tools(settings, hr, today=lambda: datetime.date(2026, 10, 16)), hr


def tok(uid: str) -> str:
    return session.issue(SECRET.encode(), uid, 900)


async def test_outcomes(settings: Settings) -> None:
    tools, hr = make(settings)
    await tools.leave_balance(tok("zhangsan"))
    await tools.leave_balance("伪造的令牌")
    await tools.leave_balance(tok("nobody"))
    await tools.leave_balance(tok("wangwu"))  # 没有岗位
    await tools.decide(tok("zhangsan"), "HR-LAP-1", "approve")  # 不是审批人
    hr.down = True
    await tools.leave_balance(tok("zhangsan"))
    got = [(a["tool"], a["outcome"], a["reason"], a["userid"], a["employee"]) for a in hr.audits]
    assert got == [
        ("leave_balance", "ok", "", "zhangsan", "E1"),
        ("leave_balance", "denied", "session", "", ""),
        ("leave_balance", "denied", "no_employee", "nobody", ""),
        ("leave_balance", "denied", "switched_off", "wangwu", "E3"),
        ("decide", "rejected", "", "zhangsan", "E1"),
        ("leave_balance", "error", "frappe_unavailable", "zhangsan", ""),
    ]
    assert hr.audits[4]["target"] == "HR-LAP-1"
    assert {a["channel"] for a in hr.audits} == {"dify"}


async def test_confirm_records_leave_id_not_reason(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    tools, hr = make(settings)
    caplog.set_level(logging.INFO, logger="frappe_mcp.audit")
    draft = await tools.draft_leave(tok("zhangsan"), "年假", "2026-11-02", "2026-11-03", "家里有事")
    out = await tools.confirm_leave("zhangsan", draft["draft_token"])
    await tools.confirm_leave("lisi", draft["draft_token"])
    last = hr.audits[-2]
    assert (last["tool"], last["outcome"], last["employee"]) == ("confirm_leave", "ok", "E1")
    assert last["target"] == out["id"] and "_employee" not in out
    assert hr.audits[-1]["outcome"] == "denied" and hr.audits[-1]["reason"] == "draft"
    logged = caplog.text
    assert "家里有事" not in logged and draft["draft_token"] not in logged
    assert json.loads(caplog.records[-1].getMessage())["tool"] == "confirm_leave"


async def test_audit_failure_does_not_block(settings: Settings) -> None:
    tools, hr = make(settings)
    hr.audit_down = True
    out = await tools.leave_balance(tok("zhangsan"))
    assert out["employee_name"] == "张三"


def test_channels_by_route(settings: Settings) -> None:
    hr = FakeHR()
    s = settings.model_copy(update={"mcp_portal_token": SecretStr(PORTAL_TOKEN)})
    with TestClient(create_app(s, hr)) as c:
        resp = c.post(
            "/v1/portal/inquiry_detail",
            json={"user_id": "zhangsan", "inquiry_id": "CRM-LEAD-1"},
            headers={"Authorization": f"Bearer {PORTAL_TOKEN}"},
        )
        assert resp.status_code == 200
        body = {"user_id": "zhangsan", "draft_token": "x"}
        c.post("/v1/leave/confirm", json=body, headers={"Authorization": f"Bearer {TOKEN}"})
    assert [(a["channel"], a["tool"], a["target"]) for a in hr.audits] == [
        ("portal", "inquiry_detail", "CRM-LEAD-1"),
        ("dify", "confirm_leave", ""),
    ]
