"""到 Frappe HR 查员工自己的假期和请假（只走 REST，不 import Frappe 代码）。

只取回答需要的字段，不取手机号、身份证、银行卡、薪资；
审批人只返回姓名，不返回邮箱。
"""

import datetime
import json
import re
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from frappe_mcp.config import Settings

LEAVE_APP = "hrms.hr.doctype.leave_application.leave_application"
LEAVE_DETAILS = f"{LEAVE_APP}.get_leave_details"
LEAVE_APPROVER = f"{LEAVE_APP}.get_leave_approver"
# X2 给员工前台的审批方法：以审批人本人身份走原生 Workflow，只有 X4 接口账号能调
PORTAL_DECIDE = "enterprise_base.wecom.portal_actions.decide"
# 询盘中心的方法（X1/X2 App 的 marketing/api.py）：只有 X4 接口账号能调，方法内校验负责人
INQUIRY_API = "enterprise_base.marketing.api."
INQUIRY_METHODS = ("intake", "my_inquiries", "inquiry", "follow_up", "stats")
# X4 工具调用审计（duties/audit.py），只有 X4 接口账号能写
AUDIT_METHOD = "enterprise_base.duties.audit.record"
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
    # 两级审批（GPT P2-G1 工作流 EB Leave Review）的当前环节
    "eb_leave_state",
]

# 岗位上的助手功能开关（X1 加的勾选框），哪个工具对应哪个开关见 tools.TOOL_SWITCHES
TOOL_SWITCH_FIELDS = (
    "eb_tool_leave_query",
    "eb_tool_leave_apply",
    "eb_tool_approval",
    "eb_tool_duties",
    "eb_tool_inquiry",
    "eb_tool_inquiry_stats",
)


class Employee(BaseModel):
    name: str
    employee_name: str
    # Frappe 用户（邮箱），只在内部用来找“待我审批”，不返回给模型
    user_id: str | None
    designation: str | None = None
    # 查这个员工用的企业微信 userid，询盘方法在 Frappe 里按它再校验一次本人
    wecom_userid: str = ""


class FrappeRejected(Exception):
    """Frappe 校验没通过（重叠、余额不足等），带异常类型和第一条提示。"""

    def __init__(self, exc_type: str, message: str) -> None:
        super().__init__(exc_type)
        self.exc_type = exc_type
        self.message = message


class HR(Protocol):
    async def employee(self, wecom_userid: str) -> Employee | None: ...

    async def leave_balance(self, employee: str, on: datetime.date) -> dict[str, Any]: ...

    async def leave_applications(
        self, filters: list[list[object]], limit: int
    ) -> list[dict[str, Any]]: ...

    async def leave_types(self) -> list[str]: ...

    async def create_leave(self, doc: dict[str, Any]) -> str: ...

    async def responsibilities(self, designation: str) -> str | None: ...

    async def tool_switches(self, designation: str) -> dict[str, bool | None] | None: ...

    async def decide(self, wecom_userid: str, leave: str, action: str) -> dict[str, Any]: ...

    async def inquiry_call(self, method: str, body: dict[str, Any]) -> dict[str, Any]: ...

    async def audit(self, entry: dict[str, str]) -> None: ...


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
            ["name", "employee_name", "user_id", "designation"],
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
            designation=row.get("designation") or None,
            wecom_userid=wecom_userid,
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

    async def leave_types(self) -> list[str]:
        rows = await self._list("Leave Type", [], ["name"], 100, "name asc")
        return [str(r["name"]) for r in rows]

    async def create_leave(self, doc: dict[str, Any]) -> str:
        """建一张待审批（Open、未提交）的请假单，返回单号。

        REST 新建时 Frappe 不会像页面那样自动带出审批人，这里按 HRMS 自己的规则
        （员工上的请假审批人，没有就用部门的）取一次再填进去。
        """
        resp = await self._client.get(
            f"{self._base}/api/method/{LEAVE_APPROVER}",
            headers=self._headers,
            params={"employee": doc["employee"]},
        )
        resp.raise_for_status()
        approver = resp.json().get("message")
        body = {**doc, "doctype": "Leave Application", "status": "Open"}
        if approver:
            body["leave_approver"] = approver
        resp = await self._client.post(
            f"{self._base}/api/resource/Leave Application", headers=self._headers, json=body
        )
        if resp.status_code == 417:
            raise _rejected(resp.json())
        resp.raise_for_status()
        return str(resp.json()["data"]["name"])

    async def responsibilities(self, designation: str) -> str | None:
        """岗位职责原文（X1 在 Designation 上加的富文本字段），岗位不存在返回 None。"""
        rows = await self._list(
            "Designation", [["name", "=", designation]], ["name", "eb_responsibilities"], 1
        )
        if not rows:
            return None
        return str(rows[0].get("eb_responsibilities") or "")

    async def tool_switches(self, designation: str) -> dict[str, bool | None] | None:
        """岗位上的助手功能开关（X1 在 Designation 上加的勾选框），岗位不存在返回 None。

        X1 的 X4 接口白名单要放行这些字段，所以 X1 要先于本服务升级；
        还没升级时 Frappe 拒绝查询，按 HR 系统不可用处理。没写过值的开关原样返回 None。
        """
        rows = await self._list(
            "Designation", [["name", "=", designation]], ["name", *TOOL_SWITCH_FIELDS], 1
        )
        if not rows:
            return None
        row = rows[0]
        return {f: None if row.get(f) is None else bool(row[f]) for f in TOOL_SWITCH_FIELDS}

    async def decide(self, wecom_userid: str, leave: str, action: str) -> dict[str, Any]:
        """以审批人本人身份通过或退回；Frappe 按卡片审批同样的规则重新校验审批人。"""
        resp = await self._client.post(
            f"{self._base}/api/method/{PORTAL_DECIDE}",
            headers=self._headers,
            json={"userid": wecom_userid, "leave": leave, "action": action},
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json().get("message") or {}
        return result

    async def inquiry_call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        """调询盘方法，返回方法的结果（字典）。method 只能是 INQUIRY_METHODS 之一。"""
        if method not in INQUIRY_METHODS:
            raise ValueError(method)
        resp = await self._client.post(
            f"{self._base}/api/method/{INQUIRY_API}{method}", headers=self._headers, json=body
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json().get("message") or {}
        return result

    async def audit(self, entry: dict[str, str]) -> None:
        resp = await self._client.post(
            f"{self._base}/api/method/{AUDIT_METHOD}", headers=self._headers, json=entry
        )
        resp.raise_for_status()
        if (resp.json().get("message") or {}).get("ok") is not True:
            raise ValueError("audit rejected")

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


def _rejected(data: dict[str, Any]) -> FrappeRejected:
    message = ""
    try:
        first = json.loads(json.loads(data.get("_server_messages") or "[]")[0])
        message = re.sub(r"<[^>]+>", "", str(first.get("message", ""))).strip()
    except (ValueError, IndexError, TypeError, AttributeError):
        pass
    return FrappeRejected(str(data.get("exc_type") or ""), message[:200])
