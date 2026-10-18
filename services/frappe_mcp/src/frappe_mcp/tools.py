"""MCP 工具的实现：先验会话令牌确定是谁，再只查这个人自己的数据。"""

import datetime
import html
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx

from frappe_mcp import session
from frappe_mcp.config import Settings
from frappe_mcp.frappe import HR, Employee, FrappeRejected

log = logging.getLogger("frappe_mcp")

NO_EMPLOYEE = "未找到你的在职员工信息，请联系 HR 核对。"
UNAVAILABLE = "HR 系统暂时不可用，请稍后再试。"
CONFIRM_WORD = "确认请假"
# Frappe 常见校验错误换成员工看得懂的话，其他的转述 Frappe 的提示
REJECTED = {
    "OverlapError": "这段时间你已经有请假申请了，可以先查一下自己的请假记录。",
    "InsufficientLeaveBalanceError": "假期余额不足，可以先查一下余额或换一种假期类型。",
    "LinkValidationError": "假期类型不存在，请换一种假期类型。",
}
MAX_REASON = 140
MAX_DAYS = 90
NO_DESIGNATION = "你的员工档案里还没有岗位，请联系 HR 补上。"
NO_DUTIES = "你的岗位还没有填写岗位职责，请联系 HR。"
# 岗位职责是 HR 录的富文本，转成纯文本后最多给模型这么多字
MAX_DUTIES = 3000


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
            # 两级审批：主管只看停在主管环节的单，HR 只看已转到 HR 环节、指定给自己的单
            rows: list[dict[str, Any]] = []
            for field, state in (("leave_approver", STATE_MANAGER), ("eb_hr_approver", STATE_HR)):
                rows += await self._hr.leave_applications(
                    [
                        [field, "=", emp.user_id],
                        ["eb_leave_state", "=", state],
                        ["docstatus", "=", 0],
                    ],
                    self._s.list_limit,
                )
            unique = list({r.get("name"): r for r in rows}.values())
            unique.sort(key=lambda r: r.get("from_date") or "", reverse=True)
            items = [
                {**_leave(r), "applicant": r.get("employee_name") or ""}
                for r in unique[: self._s.list_limit]
            ]
            return {"employee_name": emp.employee_name, "applications": items}

        return await self._as_user(session_token, run)

    async def my_responsibilities(self, session_token: str) -> dict[str, Any]:
        async def run(emp: Employee) -> dict[str, Any]:
            if not emp.designation:
                return {"employee_name": emp.employee_name, "error": NO_DESIGNATION}
            text = _plain(await self._hr.responsibilities(emp.designation) or "")
            if not text:
                return {
                    "employee_name": emp.employee_name,
                    "designation": emp.designation,
                    "error": NO_DUTIES,
                }
            return {
                "employee_name": emp.employee_name,
                "designation": emp.designation,
                "responsibilities": text[:MAX_DUTIES],
                "truncated": len(text) > MAX_DUTIES,
            }

        return await self._as_user(session_token, run)

    async def draft_leave(
        self, session_token: str, leave_type: str, from_date: str, to_date: str, reason: str = ""
    ) -> dict[str, Any]:
        """只生成草稿，不写 HR 系统；员工回复“确认请假”后由工作流调确认接口提交。"""
        try:
            uid = session.verify(self._secret, session_token)
        except session.SessionError as e:
            return {"error": str(e)}

        async def run(emp: Employee) -> dict[str, Any]:
            try:
                start = datetime.date.fromisoformat(from_date.strip())
                end = datetime.date.fromisoformat(to_date.strip())
            except ValueError:
                return {"error": "日期要写成 YYYY-MM-DD，例如 2026-10-20。"}
            if end < start:
                return {"error": "结束日期不能早于开始日期。"}
            if (end - start).days >= MAX_DAYS:
                return {"error": f"一次最多请 {MAX_DAYS} 天，更长的请联系 HR。"}
            kind = leave_type.strip()
            types = await self._hr.leave_types()
            if kind not in types:
                return {"error": "没有这种假期类型。", "leave_types": types}
            remaining = (await self._hr.leave_balance(emp.name, self._today())).get(kind, {})
            draft = {
                "leave_type": kind,
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "reason": reason.strip()[:MAX_REASON],
            }
            token = session.sign(
                self._secret,
                session.LEAVE_DRAFT,
                {"uid": uid, "emp": emp.name, **draft},
                self._s.draft_ttl_seconds,
            )
            return {
                "employee_name": emp.employee_name,
                "draft": draft,
                "remaining": remaining.get("remaining_leaves") if remaining else None,
                "draft_token": token,
                "next_step": f"还没有提交。请员工核对后回复“{CONFIRM_WORD}”，"
                f"{self._s.draft_ttl_seconds // 60} 分钟内有效。",
            }

        return await self._as_user(session_token, run)

    async def confirm_leave(self, user_id: str, draft_token: str) -> dict[str, Any]:
        """员工本人回复确认后，由工作流（不是模型）调用，把草稿原样提交到 HR 系统。"""
        try:
            draft = session.unsign(self._secret, session.LEAVE_DRAFT, draft_token)
        except session.SessionError:
            return {"ok": False, "error": "请假草稿无效或已过期，请重新发起。"}
        if draft["uid"] != user_id:
            return {"ok": False, "error": "请假草稿无效或已过期，请重新发起。"}
        try:
            emp = await self._hr.employee(user_id)
            if emp is None or emp.name != draft.get("emp"):
                return {"ok": False, "error": NO_EMPLOYEE}
            name = await self._hr.create_leave(
                {
                    "employee": emp.name,
                    "leave_type": draft["leave_type"],
                    "from_date": draft["from_date"],
                    "to_date": draft["to_date"],
                    "description": (draft.get("reason") or "") + "（企业微信助手提交）",
                    "posting_date": self._today().isoformat(),
                }
            )
        except FrappeRejected as e:
            log.info("leave rejected: %s", e.exc_type)
            msg = REJECTED.get(e.exc_type) or f"HR 系统没有接受这张请假单：{e.message}"
            return {"ok": False, "error": msg}
        except httpx.HTTPError as e:
            log.warning("frappe unavailable: %s", type(e).__name__)
            return {"ok": False, "error": UNAVAILABLE}
        return {
            "ok": True,
            "id": name,
            "message": f"已提交请假申请 {name}（{draft['leave_type']}，"
            f"{draft['from_date']} 至 {draft['to_date']}），等待审批。",
        }

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


STATE_MANAGER, STATE_HR, STATE_RETURNED = "EB Leave Manager", "EB Leave HR", "EB Leave Returned"
_STAGES = {STATE_MANAGER: "manager", STATE_HR: "hr", STATE_RETURNED: "returned"}


def _leave(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("name") or "",
        "leave_type": r.get("leave_type") or "",
        "from_date": r.get("from_date") or "",
        "to_date": r.get("to_date") or "",
        "days": r.get("total_leave_days") or 0,
        "status": r.get("status") or "",
        "approver": r.get("leave_approver_name") or "",
        # 审批中的单停在哪个环节：manager 主管、hr HR、returned 退回本人修改；其余为空
        "stage": _STAGES.get(r.get("eb_leave_state") or "", ""),
    }


_BREAK = re.compile(r"<\s*(br|/p|/li|/div|/h[1-6]|/tr)\b[^>]*>", re.I)
_ITEM = re.compile(r"<\s*li\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def _plain(raw: str) -> str:
    """富文本转纯文本：保留分段和列表，去掉标签和多余空行。"""
    text = _ITEM.sub("- ", _BREAK.sub("\n", raw))
    text = html.unescape(_TAG.sub("", text)).replace("\xa0", " ")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
