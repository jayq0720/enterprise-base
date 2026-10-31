"""X4 工具调用的审计记录：谁、从哪个入口、调了哪个工具、结果如何、操作了哪张单。

只由 X4 接口账号写（record），HR 经理和系统管理员在“EB Audit Log”列表只读查看；
不记请假事由、跟进内容、客户联系方式这类正文。保留天数用站点配置
eb_audit_retention_days（默认 180），每天清理一次。
"""

import re

import frappe

DT = "EB Audit Log"
CHANNELS = ("dify", "portal", "intake")
OUTCOMES = ("ok", "rejected", "denied", "error")
_TOOL = re.compile(r"^[a-z_]{1,40}$")
_SHORT = re.compile(r"^[A-Za-z0-9_.@:-]{0,140}$")
DEFAULT_RETENTION = 180


def _caller_is_x4():
    user = frappe.session.user
    return user not in {"Administrator", "Guest"} and "EB X4" in frappe.get_roles(user)


@frappe.whitelist(methods=["POST"])
def record(channel="", tool="", outcome="", reason="", userid="", employee="", target=""):
    if not _caller_is_x4():
        frappe.throw("只有 X4 接口账号可以调用。", frappe.PermissionError)
    values = dict(reason=reason, userid=userid, employee=employee, target=target)
    if (
        channel not in CHANNELS
        or outcome not in OUTCOMES
        or not isinstance(tool, str)
        or not _TOOL.match(tool)
        or not all(isinstance(v, str) and _SHORT.match(v) for v in values.values())
    ):
        return {"ok": False, "reason": "invalid"}
    if employee and not frappe.db.exists("Employee", employee):
        values["employee"] = ""
    frappe.get_doc(dict(doctype=DT, channel=channel, tool=tool, outcome=outcome, **values)).insert(
        ignore_permissions=True
    )
    return {"ok": True}


def purge():
    days = frappe.conf.get("eb_audit_retention_days") or DEFAULT_RETENTION
    try:
        days = max(30, int(days))
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION
    frappe.db.delete(DT, {"creation": ("<", frappe.utils.add_days(frappe.utils.now(), -days))})
