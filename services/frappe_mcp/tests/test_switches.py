"""按岗位开关助手功能（P2-C2）。"""

import json

import httpx

from frappe_mcp.config import Settings
from frappe_mcp.frappe import TOOL_SWITCH_FIELDS, FrappeHR
from frappe_mcp.tools import UNAVAILABLE, Tools

from .test_tools import draft, make, tok


async def test_switched_off_tool_is_refused(settings: Settings) -> None:
    tools, hr = make(settings)
    hr.switches["出纳"] = {"eb_tool_leave_query": False, "eb_tool_duties": True}
    out = await tools.leave_balance(tok("zhangsan"))
    assert out == {
        "employee_name": "张三",
        "error": "你的岗位没有开通“查假期和请假记录”，需要的话请联系 HR。",
    }
    assert "error" in await tools.my_leave_applications(tok("zhangsan"))
    # 没查 HR 数据
    assert hr.calls == []
    # 打开的功能照常
    assert "responsibilities" in await tools.my_responsibilities(tok("zhangsan"))


async def test_unset_switch_or_no_designation_means_on(settings: Settings) -> None:
    tools, hr = make(settings)
    # 岗位没有配开关
    assert "balances" in await tools.leave_balance(tok("zhangsan"))
    # 李四没有岗位
    hr.switches["出纳"] = {"eb_tool_approval": False}
    assert "applications" in await tools.pending_my_approval(tok("lisi"))
    assert "error" in await tools.pending_my_approval(tok("zhangsan"))


async def test_leave_apply_switch_covers_draft_and_confirm(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await draft(tools)
    # 起草后 HR 关掉了“发起请假”，确认时不再提交
    hr.switches["出纳"] = {"eb_tool_leave_apply": False}
    done = await tools.confirm_leave("zhangsan", out["draft_token"])
    assert done["ok"] is False and "发起请假" in done["error"] and hr.created == []
    assert "发起请假" in (await draft(tools))["error"]


async def test_frappe_reads_switches_from_designation(settings: Settings) -> None:
    seen: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.url.params))
        if "会计" in req.url.params["filters"]:
            return httpx.Response(200, json={"data": []})
        row = {
            "name": "出纳",
            "eb_tool_duties": 0,
            "eb_tool_approval": 1,
            "eb_tool_leave_query": None,
        }
        return httpx.Response(200, json={"data": [{**row, "eb_tool_leave_apply": 1}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        hr = FrappeHR(settings, client)
        assert await hr.tool_switches("出纳") == {
            "eb_tool_leave_query": True,
            "eb_tool_leave_apply": True,
            "eb_tool_approval": True,
            "eb_tool_duties": False,
        }
        assert await hr.tool_switches("会计") == {}
    # 按 X1 白名单的方式查：列表接口、指定字段、按名称精确过滤
    assert json.loads(seen[0]["fields"]) == ["name", *TOOL_SWITCH_FIELDS]
    assert json.loads(seen[0]["filters"]) == [["name", "=", "出纳"]]


async def test_frappe_refusal_is_not_treated_as_on(settings: Settings) -> None:
    """X1 还没放行开关字段时 Frappe 返回 403：报不可用，不默认放开。"""

    def handler(req: httpx.Request) -> httpx.Response:
        if "eb_tool_duties" in req.url.params.get("fields", ""):
            return httpx.Response(403, json={})
        rows = [
            {
                "name": "E1",
                "employee_name": "张三",
                "user_id": "zs@example.com",
                "designation": "出纳",
            }
        ]
        return httpx.Response(200, json={"data": rows})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tools = Tools(settings, FrappeHR(settings, client))
        assert await tools.leave_balance(tok("zhangsan")) == {"error": UNAVAILABLE}
