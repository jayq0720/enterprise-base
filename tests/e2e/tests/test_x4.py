"""X4 工具按会话令牌只查本人：岗位职责、假期、待我审批；不返回敏感字段；岗位关掉的功能不能用。"""

from e2e.clients import X4, Frappe
from e2e.world import World

from .conftest import assert_clean


def test_responsibilities_are_own(x4: X4, world: World) -> None:
    a = x4.call(world.a.wecom, "my_responsibilities")
    b = x4.call(world.b.wecom, "my_responsibilities")
    assert a["designation"] == world.a.designation and world.a.duties in a["responsibilities"]
    assert b["designation"] == world.b.designation and world.a.duties not in b["responsibilities"]
    assert "error" in x4.call(f"{world.tag.lower()}-nobody", "my_responsibilities")
    assert_clean(world, a, b)


def test_queries_never_leak(x4: X4, world: World) -> None:
    outputs = [
        x4.call(person.wecom, tool)
        for person in (world.a, world.b)
        for tool in ("leave_balance", "my_leave_applications", "pending_my_approval")
    ]
    assert all("error" not in o for o in outputs), outputs
    assert_clean(world, outputs)


def test_forged_token_rejected(x4: X4, world: World) -> None:
    out = x4.call(world.a.wecom, "leave_balance")
    assert "error" not in out
    # 模型如果自己编一个令牌，什么都查不到
    init = {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "e", "version": "0"},
    }
    x4._rpc("initialize", init)
    forged = x4._rpc(
        "tools/call", {"name": "leave_balance", "arguments": {"session_token": world.a.wecom}}
    )
    assert "error" in forged["content"][0]["text"]


def test_designation_switch_off(x4: X4, frappe: Frappe, world: World) -> None:
    # HR 在岗位上关掉“查岗位职责”“查假期”，下一次调用就拒绝，同部门别的岗位不受影响
    off = {"eb_tool_duties": 0, "eb_tool_leave_query": 0}
    frappe.update("Designation", world.b.designation, off)
    try:
        for tool in ("my_responsibilities", "leave_balance", "my_leave_applications"):
            out = x4.call(world.b.wecom, tool)
            assert "没有开通" in out.get("error", ""), out
            assert not {"responsibilities", "balances", "applications"} & out.keys()
        assert "error" not in x4.call(world.b.wecom, "pending_my_approval")
        assert "error" not in x4.call(world.a.wecom, "my_responsibilities")
    finally:
        frappe.update("Designation", world.b.designation, {k: 1 for k in off})
    assert "error" not in x4.call(world.b.wecom, "my_responsibilities")
