"""按岗位开关助手功能（P2-C2）。"""

import json
from typing import Any

import httpx

from frappe_mcp.config import Settings
from frappe_mcp.frappe import TOOL_SWITCH_FIELDS, FrappeHR
from frappe_mcp.tools import NO_DESIGNATION, UNAVAILABLE, Tools

from .test_tools import draft, make, tok


async def test_switched_off_tool_is_refused(settings: Settings) -> None:
    tools, hr = make(settings)
    hr.switches["出纳"]["eb_tool_leave_query"] = False
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


async def call_all(tools: Tools, uid: str) -> list[dict[str, Any]]:
    return [
        await tools.leave_balance(tok(uid)),
        await tools.my_leave_applications(tok(uid)),
        await tools.pending_my_approval(tok(uid)),
        await tools.my_responsibilities(tok(uid)),
        await draft(tools, uid),
    ]


async def test_no_or_unknown_designation_is_refused(settings: Settings) -> None:
    tools, hr = make(settings)
    # 王五没有岗位；张三的岗位被删掉了
    del hr.switches["出纳"]
    for uid in ("wangwu", "zhangsan"):
        for out in await call_all(tools, uid):
            assert out["error"] == NO_DESIGNATION and "draft_token" not in out
    assert hr.calls == []


async def test_unset_switch_is_unavailable_not_on(settings: Settings) -> None:
    tools, hr = make(settings)
    hr.switches["出纳"] = dict.fromkeys(TOOL_SWITCH_FIELDS)
    for out in await call_all(tools, "zhangsan"):
        assert out["error"] == UNAVAILABLE and "draft_token" not in out
    assert hr.calls == []


async def test_leave_apply_checked_again_at_confirm(settings: Settings) -> None:
    tools, hr = make(settings)
    token = (await draft(tools))["draft_token"]
    for change, word in (
        (lambda: hr.switches["出纳"].update(eb_tool_leave_apply=False), "发起请假"),
        (lambda: hr.switches["出纳"].update(eb_tool_leave_apply=None), UNAVAILABLE),
        (lambda: hr.switches.pop("出纳"), NO_DESIGNATION),
    ):
        change()
        done = await tools.confirm_leave("zhangsan", token)
        assert done["ok"] is False and word in done["error"]
    # 确认阶段也没有建请假单
    assert hr.created == []
    assert "draft_token" not in await draft(tools)


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
            "eb_tool_leave_query": None,
            "eb_tool_leave_apply": True,
            "eb_tool_approval": True,
            "eb_tool_duties": False,
        }
        assert await hr.tool_switches("会计") is None
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
