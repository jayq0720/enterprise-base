import datetime

from frappe_mcp import session
from frappe_mcp.config import Settings
from frappe_mcp.tools import NO_EMPLOYEE, UNAVAILABLE, Tools

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
    }
    assert hr.calls == [("apps", [["employee", "=", "E1"], ["docstatus", "<", 2]])]


async def test_pending_approval_uses_own_user(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await tools.pending_my_approval(tok("lisi"))
    assert out["applications"][0]["applicant"] == "张三"
    filters = hr.calls[0][1]
    assert ["leave_approver", "=", "ls@example.com"] in filters  # type: ignore[operator]
    # 不把审批人邮箱返回给模型
    assert "ls@example.com" not in str(out)


async def test_frappe_down(settings: Settings) -> None:
    tools, hr = make(settings)
    hr.down = True
    assert await tools.leave_balance(tok("zhangsan")) == {"error": UNAVAILABLE}
