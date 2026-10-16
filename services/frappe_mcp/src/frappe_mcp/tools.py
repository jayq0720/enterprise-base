"""MCP 工具的实现：先验会话令牌确定是谁，再只查这个人自己的数据。"""

import datetime
import logging
from collections.abc import Callable
from typing import Any

import httpx

from frappe_mcp import session
from frappe_mcp.config import Settings
from frappe_mcp.frappe import HR, Employee

log = logging.getLogger("frappe_mcp")

NO_EMPLOYEE = "未找到你的在职员工信息，请联系 HR 核对。"
UNAVAILABLE = "HR 系统暂时不可用，请稍后再试。"


class Tools:
    def __init__(
        self,
        settings: Settings,
        hr: HR,
        today: Callable[[], datetime.date] = datetime.date.today,
    ) -> None:
        self._s = settings
        self._hr = hr
        self._secret = settings.mcp_session_secret.get_secret_value().encode()
        self._today = today

    async def leave_balance(self, session_token: str) -> dict[str, Any]:
        async def run(emp: Employee) -> dict[str, Any]:
            raw = await self._hr.leave_balance(emp.name, self._today())
            items = [
                {
                    "leave_type": leave_type,
                    "total": v.get("total_leaves", 0),
                    "taken": v.get("leaves_taken", 0),
                    "pending_approval": v.get("leaves_pending_approval", 0),
                    "remaining": v.get("remaining_leaves", 0),
                }
                for leave_type, v in raw.items()
            ]
            return {"employee_name": emp.employee_name, "balances": items}

        return await self._as_user(session_token, run)

    async def my_leave_applications(self, session_token: str) -> dict[str, Any]:
        async def run(emp: Employee) -> dict[str, Any]:
            rows = await self._hr.leave_applications(
                [["employee", "=", emp.name], ["docstatus", "<", 2]], self._s.list_limit
            )
            return {"employee_name": emp.employee_name, "applications": [_leave(r) for r in rows]}

        return await self._as_user(session_token, run)

    async def pending_my_approval(self, session_token: str) -> dict[str, Any]:
        async def run(emp: Employee) -> dict[str, Any]:
            if not emp.user_id:
                return {"employee_name": emp.employee_name, "applications": []}
            rows = await self._hr.leave_applications(
                [
                    ["leave_approver", "=", emp.user_id],
                    ["status", "=", "Open"],
                    ["docstatus", "=", 0],
                ],
                self._s.list_limit,
            )
            items = [{**_leave(r), "applicant": r.get("employee_name") or ""} for r in rows]
            return {"employee_name": emp.employee_name, "applications": items}

        return await self._as_user(session_token, run)

    async def _as_user(self, token: str, run: Callable[[Employee], Any]) -> dict[str, Any]:
        try:
            uid = session.verify(self._secret, token)
        except session.SessionError as e:
            return {"error": str(e)}
        try:
            emp = await self._hr.employee(uid)
            if emp is None:
                return {"error": NO_EMPLOYEE}
            result: dict[str, Any] = await run(emp)
            return result
        except httpx.HTTPError as e:
            log.warning("frappe unavailable: %s", type(e).__name__)
            return {"error": UNAVAILABLE}


def _leave(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("name") or "",
        "leave_type": r.get("leave_type") or "",
        "from_date": r.get("from_date") or "",
        "to_date": r.get("to_date") or "",
        "days": r.get("total_leave_days") or 0,
        "status": r.get("status") or "",
        "approver": r.get("leave_approver_name") or "",
    }
