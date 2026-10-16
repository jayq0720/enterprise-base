"""到 Frappe HR 查员工自己的假期和请假（只走 REST，不 import Frappe 代码）。

只取回答需要的字段，不取手机号、身份证、银行卡、薪资；
审批人只返回姓名，不返回邮箱。
"""

import datetime
import json
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from frappe_mcp.config import Settings

LEAVE_DETAILS = "hrms.hr.doctype.leave_application.leave_application.get_leave_details"
LEAVE_FIELDS = [
    "name",
    "employee_name",
    "leave_type",
    "from_date",
    "to_date",
    "total_leave_days",
    "status",
    "leave_approver_name",
    "posting_date",
]


class Employee(BaseModel):
    name: str
    employee_name: str
    # Frappe 用户（邮箱），只在内部用来找“待我审批”，不返回给模型
    user_id: str | None


class HR(Protocol):
    async def employee(self, wecom_userid: str) -> Employee | None: ...

    async def leave_balance(self, employee: str, on: datetime.date) -> dict[str, Any]: ...

    async def leave_applications(
        self, filters: list[list[object]], limit: int
    ) -> list[dict[str, Any]]: ...


class FrappeHR:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._s = settings
        self._client = client
        key = settings.frappe_api_key.get_secret_value()
        secret = settings.frappe_api_secret.get_secret_value()
        self._headers = {"Authorization": f"token {key}:{secret}"}
        self._base = str(settings.frappe_url).rstrip("/")

    async def employee(self, wecom_userid: str) -> Employee | None:
        rows = await self._list(
            "Employee",
            [[self._s.frappe_wecom_field, "=", wecom_userid], ["status", "=", "Active"]],
            ["name", "employee_name", "user_id"],
            2,
        )
        if len(rows) != 1:
            # 查不到或一个 userid 对应多个员工：都按无权处理
            return None
        row = rows[0]
        return Employee(
            name=str(row["name"]),
            employee_name=str(row.get("employee_name") or ""),
            user_id=row.get("user_id") or None,
        )

    async def leave_balance(self, employee: str, on: datetime.date) -> dict[str, Any]:
        resp = await self._client.get(
            f"{self._base}/api/method/{LEAVE_DETAILS}",
            headers=self._headers,
            params={"employee": employee, "date": on.isoformat()},
        )
        resp.raise_for_status()
        allocation: dict[str, Any] = resp.json().get("message", {}).get("leave_allocation", {})
        return allocation

    async def leave_applications(
        self, filters: list[list[object]], limit: int
    ) -> list[dict[str, Any]]:
        return await self._list("Leave Application", filters, LEAVE_FIELDS, limit, "from_date desc")

    async def _list(
        self,
        doctype: str,
        filters: list[list[object]],
        fields: list[str],
        limit: int,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {
            "filters": json.dumps(filters, ensure_ascii=False),
            "fields": json.dumps(fields),
            "limit_page_length": str(limit),
        }
        if order_by:
            params["order_by"] = order_by
        resp = await self._client.get(
            f"{self._base}/api/resource/{doctype}", headers=self._headers, params=params
        )
        resp.raise_for_status()
        rows: list[dict[str, Any]] = resp.json().get("data", [])
        return rows
