"""HR 助手对话（模拟 dify-on-wechat 调 Dify）：要模型 Key 到位，没配 EB_E2E_HR_APP_KEY 时跳过。

模型的措辞会变，这里只检查工作流和 X4 决定的部分：查谁、提交了什么、有没有泄露。
"""

import datetime

from e2e.clients import Frappe, HRAssistant
from e2e.settings import E2ESettings
from e2e.world import World

from .conftest import assert_clean


def test_duties_and_balance(hr: HRAssistant, world: World) -> None:
    duties, _ = hr.say(world.a.wecom, "我的岗位职责是什么")
    assert world.a.duties in duties
    balance, _ = hr.say(world.a.wecom, "我还有几天年假")
    assert "余额" in balance or "额度" in balance
    other, _ = hr.say(f"{world.tag.lower()}-nobody", "我的岗位职责是什么")
    assert "未找到你的在职员工信息" in other
    assert_clean(world, duties, balance, other)


def _workday(days_ahead: int) -> datetime.date:
    day = datetime.date.today() + datetime.timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day


def test_leave_needs_own_confirmation(
    settings: E2ESettings, frappe: Frappe, hr: HRAssistant, world: World
) -> None:
    today = datetime.date.today()
    allocation = frappe.insert(
        "Leave Allocation",
        {
            "employee": world.a.employee,
            "leave_type": settings.leave_type,
            "from_date": f"{today.year}-01-01",
            "to_date": f"{today.year + 1}-12-31",
            "new_leaves_allocated": 10,
            "docstatus": 1,
        },
    )["name"]

    def drop_allocation() -> None:
        frappe.update("Leave Allocation", allocation, {"docstatus": 2})
        frappe.delete("Leave Allocation", allocation)

    world.cleanup.append(drop_allocation)
    day = _workday(40).isoformat()
    draft, cid = hr.say(world.a.wecom, f"我要请{settings.leave_type}，{day} 到 {day}，家里有事")
    assert "确认请假" in draft and day in draft

    def leaves() -> list[str]:
        return frappe.names(
            "Leave Application", [["employee", "=", world.a.employee], ["from_date", "=", day]]
        )

    # 别人说“确认请假”、或者 A 在新会话里说，都不会提交 A 的草稿
    hr.say(world.b.wecom, "确认请假")
    hr.say(world.a.wecom, "确认请假")
    assert leaves() == []

    done, _ = hr.say(world.a.wecom, "确认请假", cid)
    assert "已提交请假申请" in done
    names = leaves()
    assert len(names) == 1
    world.cleanup.append(lambda: frappe.delete("Leave Application", names[0]))
    leave = frappe.get("Leave Application", names[0])
    assert (leave["docstatus"], leave["status"], leave["leave_approver"]) == (
        0,
        "Open",
        world.b.user,
    )

    # 重复确认不会再提交一张
    again, _ = hr.say(world.a.wecom, "确认请假", cid)
    assert len(leaves()) == 1
    pending, _ = hr.say(world.b.wecom, "有什么要我审批的")
    assert names[0] in pending
    assert_clean(world, draft, done, again, pending)
