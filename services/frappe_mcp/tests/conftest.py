import datetime
from typing import Any

import pytest

from frappe_mcp.config import Settings
from frappe_mcp.frappe import Employee, FrappeRejected

TOKEN = "t" * 40
SECRET = "s" * 40


@pytest.fixture
def settings() -> Settings:
    return Settings(
        mcp_service_token=TOKEN,
        mcp_session_secret=SECRET,
        frappe_url="http://frappe.test",
        frappe_api_key="k",
        frappe_api_secret="s",
        mcp_allowed_hosts=["testserver"],
    )


class FakeHR:
    def __init__(self) -> None:
        self.employees = {
            "zhangsan": Employee(
                name="E1", employee_name="张三", user_id="zs@example.com", designation="出纳"
            ),
            "lisi": Employee(name="E2", employee_name="李四", user_id="ls@example.com"),
        }
        self.duties = {"出纳": "<p>负责&nbsp;现金收付</p><ul><li>日记账</li><li>对账</li></ul>"}
        self.calls: list[tuple[str, object]] = []
        self.down = False
        self.reject: FrappeRejected | None = None
        self.created: list[dict[str, Any]] = []
        # 岗位 → 功能开关；没配的岗位相当于 X1 还没加开关字段
        self.switches: dict[str, dict[str, bool]] = {}

    async def employee(self, wecom_userid: str) -> Employee | None:
        if self.down:
            import httpx

            raise httpx.ConnectError("down")
        return self.employees.get(wecom_userid)

    async def leave_balance(self, employee: str, on: datetime.date) -> dict[str, Any]:
        self.calls.append(("balance", employee))
        return {
            "年假": {
                "total_leaves": 5,
                "leaves_taken": 1,
                "leaves_pending_approval": 1,
                "remaining_leaves": 3,
                "expired_leaves": 0,
            }
        }

    async def leave_applications(
        self, filters: list[list[object]], limit: int
    ) -> list[dict[str, Any]]:
        self.calls.append(("apps", filters))
        return [
            {
                "name": "HR-LAP-1",
                "employee_name": "张三",
                "leave_type": "年假",
                "from_date": "2026-10-20",
                "to_date": "2026-10-21",
                "total_leave_days": 2,
                "status": "Open",
                "leave_approver_name": "李四",
                "eb_leave_state": "EB Leave Manager",
            }
        ]

    async def leave_types(self) -> list[str]:
        return ["年假", "病假"]

    async def create_leave(self, doc: dict[str, Any]) -> str:
        if self.reject:
            raise self.reject
        self.created.append(doc)
        return "HR-LAP-9"

    async def responsibilities(self, designation: str) -> str | None:
        self.calls.append(("duties", designation))
        return self.duties.get(designation)

    async def tool_switches(self, designation: str) -> dict[str, bool]:
        return self.switches.get(designation, {})
