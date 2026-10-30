"""员工前台（X6）的审批操作：只由 X4 接口账号调用，以审批人本人身份执行原生 Workflow。

身份来自前台的企业微信扫码登录，经 X4 转来企业微信 userid；这里按审批卡片同样的规则
重新校验（在职、启用、非接口账号、正是当前环节指定的审批人），不用管理员执行审批。
企业微信里已发的卡片由 card_delivery.on_update 在环节变化时自动收口。
"""

import frappe
from frappe.model.workflow import apply_workflow

from enterprise_base.wecom.card_actions import STAGES, actor

ACTIONS = {"approve": None, "return": "EB Return Leave"}
METHOD = "enterprise_base.wecom.portal_actions.decide"


def _caller_is_x4():
    user = frappe.session.user
    return user not in {"Administrator", "Guest"} and "EB X4" in frappe.get_roles(user)


def _valid(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit


@frappe.whitelist(methods=["POST"])
def decide(userid="", leave="", action=""):
    if not _caller_is_x4():
        frappe.throw("只有 X4 接口账号可以调用。", frappe.PermissionError)
    if not (_valid(userid, 64) and _valid(leave, 140) and action in ACTIONS):
        return {"ok": False, "reason": "invalid"}
    caller, messages = frappe.session.user, list(getattr(frappe.local, "message_log", []) or [])
    frappe.db.savepoint("eb_portal_action")
    reason = "forbidden"
    try:
        user = actor(userid)
        if not user or not frappe.db.sql(
            "select name from `tabLeave Application` where name=%s for update", leave
        ):
            raise frappe.PermissionError
        doc = frappe.get_doc("Leave Application", leave)
        stage = doc.get("eb_leave_state")
        if doc.docstatus != 0 or stage not in STAGES or doc.get(STAGES[stage][0]) != user:
            # 别人的单、已过了这个环节、已办完：一律按无权处理，不透露单据是否存在
            raise frappe.PermissionError
        reason = "rejected"
        frappe.set_user(user)
        apply_workflow(doc.as_json(), ACTIONS[action] or STAGES[stage][1])
        frappe.set_user(caller)
        state = frappe.db.get_value("Leave Application", leave, "eb_leave_state") or ""
        frappe.logger("portal_actions").info("leave=%s action=%s result=done", leave, action)
        return {"ok": True, "stage": state}
    except Exception:
        frappe.db.rollback(save_point="eb_portal_action")
        frappe.logger("portal_actions").info("leave=%s action=%s result=%s", leave, action, reason)
        return {"ok": False, "reason": reason}
    finally:
        frappe.set_user(caller)
        frappe.local.message_log = messages
