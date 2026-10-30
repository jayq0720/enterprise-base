"""企业微信统一回调（X2）：模拟企业微信服务器推加密消息，测审批卡片和普通聊天。

审批卡片：A 请假 → B（直属上级）的卡片点“通过” → C（HR）的卡片点“通过”，单据批完；
另一张单在 B 这里“退回”。伪造签名、申请人自己点、点别人的卡片都拒绝，单据不变。
普通聊天：消息转给 dify-on-wechat 进 HR 助手，同一员工的多条消息沿用一个会话，
同一条消息（MsgId 相同）重推不会让 HR 助手再回一次。

不连真实企业微信：卡片发不出去（会记为发送失败），按钮回调用卡片记录里的任务 ID 模拟。
没配 EB_E2E_WECOM_CALLBACK_URL 时跳过。
"""

import datetime
import secrets
import time
from typing import Any

import httpx
import pytest

from e2e.clients import Frappe, HRAssistant, wait_for
from e2e.settings import E2ESettings
from e2e.wecom import WeCom
from e2e.world import World, drop_leave

from .conftest import Flow, assert_clean

MANAGER, HR, RETURNED = "EB Leave Manager", "EB Leave HR", "EB Leave Returned"


@pytest.fixture(scope="module")
def wecom(settings: E2ESettings) -> WeCom:
    if settings.wecom_callback_url is None:
        pytest.skip("未配置 EB_E2E_WECOM_CALLBACK_URL，跳过企业微信回调测试")
    return WeCom(settings)


def _leave(settings: E2ESettings, frappe: Frappe, world: World, flow: Flow, days_ahead: int) -> str:
    day = datetime.date.today() + datetime.timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    name = str(
        frappe.insert(
            "Leave Application",
            {
                "employee": world.a.employee,
                "leave_type": settings.leave_type,
                "from_date": day.isoformat(),
                "to_date": day.isoformat(),
                "description": f"{world.tag} 企业微信卡片测试",
                "status": "Open",
            },
        )["name"]
    )

    def drop() -> None:
        if frappe.get("Leave Application", name)["docstatus"] == 1:
            # 已批的单只能由 HR 审批人走“撤销”动作，管理员也不能直接改状态
            frappe.as_user(flow.hr_user).apply_workflow(
                "Leave Application", name, "EB Cancel Leave"
            )
        drop_leave(frappe, name)

    world.cleanup.append(drop)
    return name


def _card(frappe: Frappe, leave: str, stage: str) -> dict[str, Any]:
    """进入某个审批环节时 Frappe 生成的卡片记录（任务 ID 就是记录名）。

    等后台发卡任务做完（Sent，测试环境连不上企业微信时是 Failed）再返回：真实员工
    收到卡片才会点，测试点得比发卡任务还快会撞上它写卡片记录。
    """

    def find() -> dict[str, Any] | None:
        names = frappe.names(
            "EB Approval Card", [["leave_application", "=", leave], ["stage", "=", stage]]
        )
        if len(names) != 1:
            return None
        card = frappe.get("EB Approval Card", names[0])
        return card if card["status"] in {"Sent", "Failed"} else None

    return wait_for(find, 30, 1)


def _state(frappe: Frappe, leave: str) -> tuple[str, int, str]:
    doc = frappe.get("Leave Application", leave)
    return str(doc.get("eb_leave_state")), int(doc["docstatus"]), str(doc["status"])


def _ok(r: httpx.Response) -> None:
    assert (r.status_code, r.text) == (200, "success")


def test_card_two_level_approval(
    settings: E2ESettings, frappe: Frappe, world: World, wecom: WeCom, flow: Flow
) -> None:
    leave = _leave(settings, frappe, world, flow, 50)
    card = _card(frappe, leave, MANAGER)
    assert (card["recipient"], card["userid"]) == (world.b.user, world.b.wecom)
    task = str(card["name"])

    # 伪造签名、申请人自己点、HR 点上级的卡片：都拒绝，单据还在上级环节
    assert (
        wecom.post(wecom.card_click(world.b.wecom, task, "approve"), forge=True).status_code == 403
    )
    for userid in (world.a.wecom, flow.hr_wecom):
        assert wecom.post(wecom.card_click(userid, task, "approve")).status_code == 403
    assert _state(frappe, leave) == (MANAGER, 0, "Open")

    _ok(wecom.post(wecom.card_click(world.b.wecom, task, "approve")))
    assert _state(frappe, leave) == (HR, 0, "Open")
    done = frappe.get("EB Approval Card", task)
    assert (done["status"], done["result"], done["handled_by"]) == ("Done", "已通过", world.b.user)

    hr_card = _card(frappe, leave, HR)
    assert (hr_card["recipient"], hr_card["userid"]) == (flow.hr_user, flow.hr_wecom)
    hr_task = str(hr_card["name"])
    # 上级不能替 HR 点；上级的旧卡片重复点只确认，不再审批
    assert wecom.post(wecom.card_click(world.b.wecom, hr_task, "approve")).status_code == 403
    _ok(wecom.post(wecom.card_click(world.b.wecom, task, "approve")))
    assert _state(frappe, leave) == (HR, 0, "Open")

    _ok(wecom.post(wecom.card_click(flow.hr_wecom, hr_task, "approve")))
    assert _state(frappe, leave) == ("EB Leave Approved", 1, "Approved")
    # 重复点不会再办一次
    _ok(wecom.post(wecom.card_click(flow.hr_wecom, hr_task, "approve")))
    assert _state(frappe, leave) == ("EB Leave Approved", 1, "Approved")
    assert_clean(world, card, done, hr_card)


def test_card_return(
    settings: E2ESettings, frappe: Frappe, world: World, wecom: WeCom, flow: Flow
) -> None:
    leave = _leave(settings, frappe, world, flow, 60)
    task = str(_card(frappe, leave, MANAGER)["name"])
    _ok(wecom.post(wecom.card_click(world.b.wecom, task, "return")))
    assert _state(frappe, leave) == (RETURNED, 0, "Open")
    card = frappe.get("EB Approval Card", task)
    assert (card["status"], card["result"]) == ("Done", "已退回")


def test_chat_goes_to_hr_assistant_in_one_conversation(
    settings: E2ESettings, world: World, wecom: WeCom, hr: HRAssistant
) -> None:
    if not settings.wecom_chat:
        pytest.skip("EB_E2E_WECOM_CHAT=false：聊天入口没起，跳过")
    # 同一个员工别的测试也直接问过 HR 助手，只看这个测试发出去的消息
    before = {m["id"] for m in hr.messages(world.a.wecom)}

    def new() -> list[dict[str, Any]]:
        return [m for m in hr.messages(world.a.wecom) if m["id"] not in before]

    first = str(secrets.randbits(62))
    for content, msg_id in (
        ("我的岗位职责是什么", first),
        ("我还有几天年假", str(secrets.randbits(62))),
    ):
        # 入口先回空包或加密回复，真正的回答由 dify-on-wechat 异步调企业微信接口发出
        assert wecom.post(wecom.text(world.a.wecom, content, msg_id)).status_code == 200
        if msg_id == first:
            wait_for(lambda: new() or None, 120, 3)

    def two() -> list[dict[str, Any]] | None:
        msgs = new()
        return msgs if len(msgs) >= 2 else None

    messages = wait_for(two, 120, 3)
    # 两条消息都在 A 的同一个会话里，问职责那条回答的是 A 本人的岗位职责
    assert [m["query"] for m in messages] == ["我的岗位职责是什么", "我还有几天年假"]
    assert len({m["conversation_id"] for m in messages}) == 1
    assert world.a.duties in messages[0]["answer"]
    # 企业微信重推同一条消息（MsgId 相同）：入口去重，HR 助手不再处理
    assert wecom.post(wecom.text(world.a.wecom, "我的岗位职责是什么", first)).status_code == 200
    time.sleep(15)
    assert len(new()) == len(messages)
    assert_clean(world, messages)
