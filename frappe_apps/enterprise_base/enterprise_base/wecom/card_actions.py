"""仅由验签分发器调用；不白名单公开，不用管理员执行审批。"""

from datetime import UTC, datetime

import frappe
from frappe.model.workflow import apply_workflow

DT = "EB Approval Card"
STAGES = {
    "EB Leave Manager": ("leave_approver", "EB Manager Approve"),
    "EB Leave HR": ("eb_hr_approver", "EB HR Approve"),
}


def actor(userid):
    rows = frappe.db.sql(
        "select e.user_id, e.status, u.enabled, u.user_type from `tabEmployee` e "
        "left join `tabUser` u on u.name=e.user_id where e.wecom_userid=%s for update",
        userid,
        as_dict=True,
    )
    if len(rows) != 1:
        return None
    row = rows[0]
    if (
        row.status != "Active"
        or not row.enabled
        or row.user_type != "System User"
        or row.user_id in {None, "Administrator", "Guest"}
    ):
        return None
    roles = set(frappe.get_roles(row.user_id))
    return row.user_id if "Employee" in roles and not roles & {"EB Integration", "EB X4"} else None


def locked(task):
    leave = frappe.db.get_value(DT, task, "leave_application")
    if not leave or not frappe.db.sql(
        "select name from `tabLeave Application` where name=%s for update", leave
    ):
        return None
    if not frappe.db.sql("select name from `tabEB Approval Card` where name=%s for update", task):
        return None
    return frappe.get_doc(DT, task)


def enqueue(card):
    frappe.enqueue(
        "enterprise_base.wecom.card_delivery.process", card=card, enqueue_after_commit=True
    )


def apply(event):
    if not isinstance(event, dict):
        return False
    cfg = frappe.conf.get("eb_wecom_cards")
    if (
        not isinstance(cfg, dict)
        or cfg.get("enabled") is not True
        or not frappe.db.exists("DocType", DT)
    ):
        return False
    if not all(isinstance(cfg.get(k), str) and cfg[k] for k in ("corpid", "agentid")):
        return False
    if any(
        event.get(k) != v
        for k, v in dict(
            ToUserName=cfg.get("corpid"),
            AgentID=cfg.get("agentid"),
            MsgType="event",
            Event="template_card_event",
            CardType="button_interaction",
        ).items()
    ):
        return False
    if event.get("EventKey") not in {"approve", "return"} or not all(
        isinstance(event.get(k), str)
        and 0 < len(event[k]) <= {"TaskId": 128, "FromUserName": 64, "ResponseCode": 4096}[k]
        for k in ("TaskId", "FromUserName", "ResponseCode")
    ):
        return False
    previous, messages = frappe.session.user, list(getattr(frappe.local, "message_log", []) or [])
    frappe.db.savepoint("eb_card_action")
    leave, action = "-", event["EventKey"]
    try:
        user = actor(event["FromUserName"])
        if not user:
            raise frappe.PermissionError
        card = locked(event["TaskId"])
        if (
            not card
            or card.recipient != user
            or card.userid.casefold() != event["FromUserName"].casefold()
        ):
            raise frappe.PermissionError
        leave = card.leave_application
        if card.status == "Done":
            if card.update_status in {"Failed", "Expired"}:
                card.response_code, card.sent_at = (
                    event["ResponseCode"],
                    datetime.now(UTC).isoformat(),
                )
                card.update_status, card.attempts = "Pending", 0
                card.save(ignore_permissions=True)
                enqueue(card.name)
            return True
        doc = frappe.get_doc("Leave Application", leave)
        stage = doc.get("eb_leave_state")
        if stage not in STAGES or card.stage != stage or doc.get(STAGES[stage][0]) != user:
            raise frappe.PermissionError
        card.response_code, card.sent_at = event["ResponseCode"], datetime.now(UTC).isoformat()
        card.save(ignore_permissions=True)
        frappe.set_user(user)
        apply_workflow(
            doc.as_json(), STAGES[stage][1] if action == "approve" else "EB Return Leave"
        )
        card.reload()
        card.update(
            dict(
                status="Done",
                result="已通过" if action == "approve" else "已退回",
                handled_by=user,
                update_status="Pending",
                attempts=0,
            )
        )
        card.save(ignore_permissions=True)
        enqueue(card.name)
        frappe.logger("wecom_cards").info("leave=%s action=%s result=%s", leave, action, "done")
        return True
    except Exception:
        frappe.db.rollback(save_point="eb_card_action")
        frappe.logger("wecom_cards").info("leave=%s action=%s result=%s", leave, action, "rejected")
        return False
    finally:
        frappe.set_user(previous)
        frappe.local.message_log = messages
