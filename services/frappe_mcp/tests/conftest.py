import datetime
from typing import Any

import pytest

from frappe_mcp.config import Settings
from frappe_mcp.frappe import Employee

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
            "zhangsan": Employee(name="E1", employee_name="张三", user_id="zs@example.com"),
            "lisi": Employee(name="E2", employee_name="李四", user_id="ls@example.com"),
        }
        self.calls: list[tuple[str, object]] = []
        self.down = False

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
            }
        ]
