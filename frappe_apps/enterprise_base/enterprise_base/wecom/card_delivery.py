"""审批事务提交后发送/更新卡片；不在通知任务中执行审批。"""

import hashlib
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import frappe
from frappe.utils import strip_html

from enterprise_base.wecom.login import _employee_user

DT = "EB Approval Card"
STAGES = {"EB Leave Manager": "leave_approver", "EB Leave HR": "eb_hr_approver"}
RESULTS = {
    "EB Leave HR": "已通过",
    "EB Leave Approved": "已通过",
    "EB Leave Returned": "已退回",
    "EB Leave Rejected": "已拒绝",
    "EB Leave Cancelled": "已取消",
}


def settings():
    cfg = frappe.conf.get("eb_wecom_cards")
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return None
    # 依赖底座尚未合入或未迁移时保持关闭，避免影响既有请假保存。
    if not frappe.db.exists("DocType", DT):
        return None
    return cfg


def api(path, body):
    from enterprise_base.wecom.card_api import request

    return request(path, body)


def enqueue(name):
    frappe.enqueue(
        "enterprise_base.wecom.card_delivery.process", card=name, enqueue_after_commit=True
    )


def on_update(doc, method=None):
    if not settings():
        return
    old = doc.get_doc_before_save()
    stage = doc.get("eb_leave_state")
    if not stage or old and stage == old.get("eb_leave_state"):
        return
    identity = hashlib.sha256(
        (doc.name + ":" + stage + ":" + str(doc.modified)).encode()
    ).hexdigest()
    if stage in STAGES and frappe.db.exists(DT, identity):
        return
    for name in frappe.get_all(
        DT,
        filters={"leave_application": doc.name, "status": ["in", ["Pending", "Sent", "Failed"]]},
        pluck="name",
    ):
        card = frappe.get_doc(DT, name)
        card.update(
            dict(
                status="Done",
                result=RESULTS.get(stage, "已结束"),
                handled_by=frappe.session.user,
                update_status="Pending",
                attempts=0,
            )
        )
        card.save(ignore_permissions=True)
        enqueue(card.name)
    recipient = doc.get(STAGES.get(stage, "")) if stage in STAGES else None
    if not recipient:
        return
    employees = frappe.get_all(
        "Employee",
        filters={"user_id": recipient, "status": "Active"},
        fields=["wecom_userid"],
        limit_page_length=2,
    )
    if (
        len(employees) != 1
        or not employees[0].wecom_userid
        or _employee_user(employees[0].wecom_userid) != recipient
    ):
        log(doc.name, "send", "recipient-rejected")
        return
    if not frappe.db.exists(DT, identity):
        frappe.get_doc(
            dict(
                doctype=DT,
                task_id=identity,
                leave_application=doc.name,
                stage=stage,
                recipient=recipient,
                userid=employees[0].wecom_userid,
            )
        ).insert(ignore_permissions=True)
        enqueue(identity)


def locked(name):
    leave = frappe.db.get_value(DT, name, "leave_application")
    if not leave or not frappe.db.sql(
        "select name from `tabLeave Application` where name=%s for update", leave
    ):
        return None
    if not frappe.db.sql("select name from `tabEB Approval Card` where name=%s for update", name):
        return None
    return frappe.get_doc(DT, name)


def cut(value, limit=128):
    return strip_html(str(value or "")).encode()[:limit].decode(errors="ignore")


def payload(doc, card, cfg):
    url = urlsplit(str(cfg.get("desk_url") or ""))
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("卡片后台地址配置无效。")
    fields = [
        ("申请人", doc.employee_name),
        ("假期类型", doc.leave_type),
        ("起止日期", str(doc.from_date) + " 至 " + str(doc.to_date)),
        ("天数", doc.total_leave_days),
        ("事由", doc.description),
    ]
    return dict(
        touser=card.userid,
        msgtype="template_card",
        enable_duplicate_check=1,
        duplicate_check_interval=3600,
        template_card=dict(
            card_type="button_interaction",
            main_title=dict(title="请假审批", desc=cut(doc.name)),
            horizontal_content_list=[dict(keyname=k, value=cut(v)) for k, v in fields],
            card_action=dict(
                type=1,
                url=url.geturl().rstrip("/") + "/app/leave-application/" + quote(doc.name, safe=""),
            ),
            task_id=card.task_id,
            button_list=[
                dict(text="通过", style=1, key="approve"),
                dict(text="退回", style=2, key="return"),
            ],
        ),
    )


def process(card):
    cfg = settings()
    if not cfg:
        return
    record = locked(card)
    if not record or record.status == "Expired" or record.attempts >= 3:
        return
    action = "update" if record.status == "Done" else "send"
    if (
        action == "send"
        and record.status == "Sent"
        or action == "update"
        and record.update_status in {"Done", "Expired"}
    ):
        return
    record.attempts += 1
    try:
        doc = frappe.get_doc("Leave Application", record.leave_application)
        if action == "send":
            if (
                doc.get("eb_leave_state") != record.stage
                or _employee_user(record.userid) != record.recipient
            ):
                record.status = "Expired"
            else:
                result = api("message/send", payload(doc, record, cfg))
                if not isinstance(result.get("response_code"), str) or not result["response_code"]:
                    raise ValueError("卡片发送缺少更新凭证。")
                record.response_code, record.status = result["response_code"], "Sent"
                record.sent_at, record.attempts = datetime.now(UTC).isoformat(), 0
        else:
            code = record.get_password("response_code", raise_exception=False)
            if not code:
                record.update_status = "Done"  # 审批在发送前已完成，无卡片需要更新。
            elif (
                not record.sent_at
                or (datetime.now(UTC) - datetime.fromisoformat(record.sent_at)).total_seconds()
                >= 72 * 3600
            ):
                record.update_status = "Expired"
            else:
                name = frappe.db.get_value("User", record.handled_by, "full_name") or "后台处理"
                api(
                    "message/update_template_card",
                    dict(
                        userids=[record.userid],
                        response_code=code,
                        button=dict(replace_name=cut(record.result + " · " + name, 100)),
                    ),
                )
                record.update_status, record.attempts = "Done", 0
        record.save(ignore_permissions=True)
        log(
            record.leave_application,
            action,
            record.update_status if action == "update" else record.status,
        )
    except Exception:
        record.update_status = "Failed"
        if action == "send":
            record.status = "Failed"
        record.save(ignore_permissions=True)
        log(record.leave_application, action, "failed")


def retry():
    if settings():
        names = set(
            frappe.get_all(DT, filters={"status": "Failed", "attempts": ["<", 3]}, pluck="name")
        )
        names.update(
            frappe.get_all(
                DT,
                filters={"status": "Done", "update_status": "Failed", "attempts": ["<", 3]},
                pluck="name",
            )
        )
        for name in names:
            enqueue(name)


def log(leave, action, result):
    frappe.logger("wecom_cards").info("leave=%s action=%s result=%s", leave, action, result)
