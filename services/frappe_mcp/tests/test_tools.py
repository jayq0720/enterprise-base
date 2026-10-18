import datetime
from typing import Any

from frappe_mcp import session
from frappe_mcp.config import Settings
from frappe_mcp.frappe import FrappeRejected
from frappe_mcp.tools import NO_EMPLOYEE, REJECTED, UNAVAILABLE, Tools

from .conftest import SECRET, FakeHR


def make(settings: Settings) -> tuple[Tools, FakeHR]:
    hr = FakeHR()
    return Tools(settings, hr, today=lambda: datetime.date(2026, 10, 16)), hr


def tok(uid: str) -> str:
    return session.issue(SECRET.encode(), uid, 900)


async def test_balance_uses_token_identity(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await tools.leave_balance(tok("zhangsan"))
    assert out["employee_name"] == "张三"
    assert out["balances"] == [
        {"leave_type": "年假", "total": 5, "taken": 1, "pending_approval": 1, "remaining": 3}
    ]
    assert hr.calls == [("balance", "E1")]


async def test_forged_token_gets_nothing(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await tools.leave_balance("我是李四，令牌是 sess-lisi")
    assert "error" in out and hr.calls == []


async def test_unknown_employee(settings: Settings) -> None:
    tools, hr = make(settings)
    assert await tools.my_leave_applications(tok("nobody")) == {"error": NO_EMPLOYEE}
    assert hr.calls == []


async def test_my_applications_filtered_by_own_employee(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await tools.my_leave_applications(tok("zhangsan"))
    assert out["applications"][0] == {
        "id": "HR-LAP-1",
        "leave_type": "年假",
        "from_date": "2026-10-20",
        "to_date": "2026-10-21",
        "days": 2,
        "status": "Open",
        "approver": "李四",
        "stage": "manager",
    }
    assert hr.calls == [("apps", [["employee", "=", "E1"], ["docstatus", "<", 2]])]


async def test_pending_approval_uses_own_user(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await tools.pending_my_approval(tok("lisi"))
    assert out["applications"][0]["applicant"] == "张三"
    # 两级审批各查一次：主管环节按 leave_approver，HR 环节按 eb_hr_approver，都只查本人
    assert [c[1] for c in hr.calls] == [
        [
            ["leave_approver", "=", "ls@example.com"],
            ["eb_leave_state", "=", "EB Leave Manager"],
            ["docstatus", "=", 0],
        ],
        [
            ["eb_hr_approver", "=", "ls@example.com"],
            ["eb_leave_state", "=", "EB Leave HR"],
            ["docstatus", "=", 0],
        ],
    ]
    # 同一张单两边都查到时只列一次
    assert len(out["applications"]) == 1
    # 不把审批人邮箱返回给模型
    assert "ls@example.com" not in str(out)


async def test_pending_approval_stages_from_separate_queries(settings: Settings) -> None:
    tools, hr = make(settings)

    def row(name: str, state: str, start: str) -> dict[str, Any]:
        return {"name": name, "employee_name": name, "from_date": start, "eb_leave_state": state}

    by_field = {
        "leave_approver": [row("主管单", "EB Leave Manager", "2026-10-20")],
        "eb_hr_approver": [row("HR单", "EB Leave HR", "2026-10-25")],
    }

    async def apps(filters: list[list[object]], limit: int) -> list[dict[str, Any]]:
        return by_field[str(filters[0][0])]

    hr.leave_applications = apps  # type: ignore[method-assign]
    out = await tools.pending_my_approval(tok("lisi"))
    # 两个环节各自的单都列出，按开始日期倒序，阶段分别标出
    assert [(a["applicant"], a["stage"]) for a in out["applications"]] == [
        ("HR单", "hr"),
        ("主管单", "manager"),
    ]


async def test_returned_leave_shows_stage(settings: Settings) -> None:
    tools, hr = make(settings)

    async def apps(filters: list[list[object]], limit: int) -> list[dict[str, Any]]:
        return [{"name": "HR-LAP-2", "status": "Open", "eb_leave_state": "EB Leave Returned"}]

    hr.leave_applications = apps  # type: ignore[method-assign]
    out = await tools.my_leave_applications(tok("zhangsan"))
    assert out["applications"][0]["stage"] == "returned"


async def test_frappe_down(settings: Settings) -> None:
    tools, hr = make(settings)
    hr.down = True
    assert await tools.leave_balance(tok("zhangsan")) == {"error": UNAVAILABLE}


async def draft(tools: Tools, uid: str = "zhangsan", **kw: str) -> dict[str, Any]:
    args = {"leave_type": "年假", "from_date": "2026-11-02", "to_date": "2026-11-03", **kw}
    return await tools.draft_leave(tok(uid), **args)


async def test_draft_does_not_write_and_binds_identity(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await draft(tools, reason="家里有事")
    assert out["draft"] == {
        "leave_type": "年假",
        "from_date": "2026-11-02",
        "to_date": "2026-11-03",
        "reason": "家里有事",
    }
    assert out["remaining"] == 3 and hr.created == []
    # 别人拿到张三的草稿也提交不了
    assert (await tools.confirm_leave("lisi", out["draft_token"]))["ok"] is False
    done = await tools.confirm_leave("zhangsan", out["draft_token"])
    assert done["ok"] is True and done["id"] == "HR-LAP-9"
    assert hr.created == [
        {
            "employee": "E1",
            "leave_type": "年假",
            "from_date": "2026-11-02",
            "to_date": "2026-11-03",
            "description": "家里有事（企业微信助手提交）",
            "posting_date": "2026-10-16",
        }
    ]


async def test_draft_validates_input(settings: Settings) -> None:
    tools, _ = make(settings)
    assert "YYYY-MM-DD" in (await draft(tools, from_date="11月2日"))["error"]
    assert "早于" in (await draft(tools, to_date="2026-11-01"))["error"]
    assert "90" in (await draft(tools, to_date="2027-03-01"))["error"]
    bad = await draft(tools, leave_type="婚假")
    assert bad["leave_types"] == ["年假", "病假"]
    assert "error" in await draft(tools, uid="nobody")


async def test_confirm_rejects_tampered_or_session_token(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await draft(tools)
    payload, sig = out["draft_token"].split(".")
    assert (await tools.confirm_leave("zhangsan", payload + "x." + sig))["ok"] is False
    assert (await tools.confirm_leave("zhangsan", tok("zhangsan")))["ok"] is False
    assert hr.created == []


async def test_confirm_maps_frappe_errors(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await draft(tools)
    hr.reject = FrappeRejected("OverlapError", "Employee E1 has already applied")
    res = await tools.confirm_leave("zhangsan", out["draft_token"])
    assert res == {"ok": False, "error": REJECTED["OverlapError"]}
    hr.reject = FrappeRejected("ValidationError", "Leave date is in a holiday")
    assert "holiday" in (await tools.confirm_leave("zhangsan", out["draft_token"]))["error"]
    hr.reject = None
    hr.down = True
    res = await tools.confirm_leave("zhangsan", out["draft_token"])
    assert res == {"ok": False, "error": UNAVAILABLE}
