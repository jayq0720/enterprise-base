import datetime

from frappe_mcp import session
from frappe_mcp.config import Settings
from frappe_mcp.tools import MAX_DUTIES, NO_DESIGNATION, NO_DUTIES, NO_EMPLOYEE, Tools, _plain

from .conftest import SECRET, FakeHR


def make(settings: Settings) -> tuple[Tools, FakeHR]:
    hr = FakeHR()
    return Tools(settings, hr, today=lambda: datetime.date(2026, 10, 16)), hr


def tok(uid: str) -> str:
    return session.issue(SECRET.encode(), uid, 900)


async def test_own_designation_only(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await tools.my_responsibilities(tok("zhangsan"))
    assert out == {
        "employee_name": "张三",
        "designation": "出纳",
        "responsibilities": "负责 现金收付\n- 日记账\n- 对账",
        "truncated": False,
    }
    # 只查令牌里那个人的岗位
    assert hr.calls == [("duties", "出纳")]


async def test_no_designation(settings: Settings) -> None:
    tools, hr = make(settings)
    out = await tools.my_responsibilities(tok("wangwu"))
    assert out == {"employee_name": "王五", "error": NO_DESIGNATION}
    assert hr.calls == []


async def test_empty_or_missing_duties(settings: Settings) -> None:
    tools, hr = make(settings)
    hr.duties["出纳"] = "<p><br></p>"
    assert (await tools.my_responsibilities(tok("zhangsan")))["error"] == NO_DUTIES
    del hr.duties["出纳"]
    assert (await tools.my_responsibilities(tok("zhangsan")))["error"] == NO_DUTIES


async def test_unknown_or_forged(settings: Settings) -> None:
    tools, hr = make(settings)
    assert await tools.my_responsibilities(tok("nobody")) == {"error": NO_EMPLOYEE}
    assert "error" in await tools.my_responsibilities("出纳")
    assert hr.calls == []


async def test_long_duties_truncated(settings: Settings) -> None:
    tools, hr = make(settings)
    hr.duties["出纳"] = "<p>" + "账" * (MAX_DUTIES + 10) + "</p>"
    out = await tools.my_responsibilities(tok("zhangsan"))
    assert len(out["responsibilities"]) == MAX_DUTIES and out["truncated"] is True


def test_plain_strips_markup() -> None:
    raw = "<h3>职责</h3><ol><li>审核<b>报销</b></li></ol><div>a&lt;b<br/>c</div><script>x</script>"
    assert _plain(raw) == "职责\n- 审核报销\na<b\nc\nx"
