"""原生 Workflow 的安装与防绕过校验，不另造审批引擎。"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.workflow import get_transitions, get_workflow_name, has_approval_access
from hrms.hr.doctype.leave_application.leave_application import get_leave_approver

from enterprise_base.permissions import x4_draft

FLOW = "EB Leave Review"
STATES = {
    "EB Leave Manager": (0, "Open"),
    "EB Leave HR": (0, "Open"),
    "EB Leave Returned": (0, "Open"),
    "EB Leave Approved": (1, "Approved"),
    "EB Leave Rejected": (1, "Rejected"),
    "EB Leave Cancelled": (2, "Cancelled"),
}


def configure():
    create_custom_fields(
        {
            "Employee": [
                dict(
                    fieldname="eb_hr_approver",
                    label="请假 HR 审批人",
                    insert_after="leave_approver",
                    fieldtype="Link",
                    options="User",
                )
            ],
            "Leave Application": [
                dict(
                    fieldname="eb_hr_approver",
                    label="HR 审批人",
                    fieldtype="Link",
                    options="User",
                    read_only=1,
                ),
                dict(
                    fieldname="eb_leave_state",
                    label="请假审批状态",
                    fieldtype="Link",
                    options="Workflow State",
                    read_only=1,
                    allow_on_submit=1,
                    no_copy=1,
                ),
            ],
        }
    )


def validate(doc, method=None):
    x4_draft(doc)
    if get_workflow_name("Leave Application") != FLOW:
        return
    user, roles = frappe.session.user, set(frappe.get_roles())
    owner = frappe.db.get_value("Employee", doc.employee, "user_id")
    if doc.is_new():
        if (
            user != "Administrator"
            and user != owner
            and not roles & {"HR Manager", "HR User", "EB X4"}
        ):
            deny()
        approver = get_leave_approver(doc.employee)
        if doc.leave_approver and doc.leave_approver != approver:
            deny()
        doc.leave_approver = approver
        doc.eb_hr_approver = frappe.db.get_value("Employee", doc.employee, "eb_hr_approver")
        doc.eb_leave_state = doc.get("eb_leave_state") or "EB Leave Manager"
        if doc.eb_leave_state != "EB Leave Manager":
            deny()
    old = doc.get_doc_before_save()
    state = doc.get("eb_leave_state")
    if state not in STATES or (int(doc.docstatus), doc.status) != STATES[state]:
        deny()
    if not old:
        return
    if any(
        doc.get(f) != old.get(f)
        for f in ("employee", "leave_approver", "eb_hr_approver", "company")
    ):
        deny()
    protected = "leave_type from_date to_date half_day half_day_date description".split()
    changed = any(doc.get(f) != old.get(f) for f in protected)
    if changed and not (old.eb_leave_state == state == "EB Leave Returned" and user == owner):
        deny()
    if state == old.eb_leave_state:
        if int(old.docstatus) != int(doc.docstatus) or old.status != doc.status:
            deny()
        return
    transitions = get_transitions(old)
    if not any(t.next_state == state and has_approval_access(user, old, t) for t in transitions):
        deny()
    if state == "EB Leave HR":
        hr = doc.eb_hr_approver
        if (
            not hr
            or not frappe.db.get_value("User", hr, "enabled")
            or not set(frappe.get_roles(hr)) & {"HR User", "HR Manager"}
            or hr == owner
            or hr == doc.leave_approver
        ):
            frappe.throw("请先由 HR Manager 配置有效且非申请人本人、非主管的 HR 审批人。")


def deny():
    frappe.throw(
        "请使用本人有权执行的请假工作流动作；不能改写审批结果或申请内容。", frappe.PermissionError
    )
