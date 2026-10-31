import datetime
from typing import Any

import pytest

from frappe_mcp.config import Settings
from frappe_mcp.frappe import TOOL_SWITCH_FIELDS, Employee, FrappeRejected

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
            "lisi": Employee(
                name="E2", employee_name="李四", user_id="ls@example.com", designation="主管"
            ),
            "wangwu": Employee(name="E3", employee_name="王五", user_id="ww@example.com"),
        }
        self.duties = {"出纳": "<p>负责&nbsp;现金收付</p><ul><li>日记账</li><li>对账</li></ul>"}
        self.calls: list[tuple[str, object]] = []
        self.down = False
        self.reject: FrappeRejected | None = None
        self.created: list[dict[str, Any]] = []
        self.decided: list[tuple[str, str, str]] = []
        # 岗位 → 功能开关；X1 升级后已有岗位的开关都是开，不在这里的岗位相当于不存在
        on = dict.fromkeys(TOOL_SWITCH_FIELDS, True)
        self.switches: dict[str, dict[str, bool | None]] = {"出纳": {**on}, "主管": {**on}}
        self.inquiry_calls: list[tuple[str, dict[str, Any]]] = []
        self.audits: list[dict[str, str]] = []
        self.audit_down = False

    async def employee(self, wecom_userid: str) -> Employee | None:
        if self.down:
            import httpx

            raise httpx.ConnectError("down")
        emp = self.employees.get(wecom_userid)
        return emp.model_copy(update={"wecom_userid": wecom_userid}) if emp else None

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

    async def tool_switches(self, designation: str) -> dict[str, bool | None] | None:
        return self.switches.get(designation)

    async def decide(self, wecom_userid: str, leave: str, action: str) -> dict[str, Any]:
        self.decided.append((wecom_userid, leave, action))
        # 只有李四是 HR-LAP-1 当前环节的审批人
        if wecom_userid == "lisi" and leave == "HR-LAP-1":
            return {"ok": True, "stage": "EB Leave HR"}
        return {"ok": False, "reason": "forbidden"}

    async def audit(self, entry: dict[str, str]) -> None:
        if self.audit_down:
            import httpx

            raise httpx.ConnectError("down")
        self.audits.append(entry)

    async def inquiry_call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        """模拟 Frappe 询盘方法：CRM-LEAD-1 属于张三，其他人一律 forbidden。"""
        if self.down:
            import httpx

            raise httpx.ConnectError("down")
        self.inquiry_calls.append((method, body))
        row = {
            "name": "CRM-LEAD-1",
            "lead_name": "Alice",
            "company_name": "Acme",
            "country": "Germany",
            "status": "Lead",
            "eb_channel": "网站表单",
            "eb_grade": "A",
            "eb_score": 70,
            "eb_product": "阀门",
            "creation": "2026-10-30 10:00:00.123456",
            "eb_first_response_at": None,
            "eb_requirement": "Need 500 pcs",
            "eb_ai_engine": "ChatGPT",
        }
        mine = body.get("userid") == "zhangsan"
        if method == "intake":
            data = body["data"]
            if not (data.get("email") or data.get("phone")):
                return {"ok": False, "reason": "no_contact"}
            return {
                "ok": True,
                "id": "CRM-LEAD-2",
                "duplicate": False,
                "grade": "B",
                "assigned": True,
            }
        if method == "my_inquiries":
            return {"ok": True, "inquiries": [row] if mine else []}
        if method == "stats":
            return {"ok": True, "days": body["days"], "total": 1, "by_status": {"Lead": 1}}
        if not mine or body.get("lead") != "CRM-LEAD-1":
            return {"ok": False, "reason": "forbidden"}
        if method == "inquiry":
            if body.get("contact"):
                row = {**row, "email_id": "alice@acme.example", "mobile_no": "+49 1"}
            return {
                "ok": True,
                "inquiry": row,
                "notes": [{"note": "已电话联系", "by": "张三", "at": ""}],
            }
        if method == "follow_up":
            if body["status"] not in ("", "Open", "Replied", "Interested", "Do Not Contact"):
                return {"ok": False, "reason": "invalid"}
            return {"ok": True, "status": body["status"] or "Lead"}
        raise AssertionError(method)
