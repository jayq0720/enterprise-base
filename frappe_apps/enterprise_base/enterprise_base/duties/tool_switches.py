"""岗位助手开关：原生字段、只补空值的升级和服务端写入边界。"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint

TOOLS = (
    ("eb_tool_leave_query", "助手可查假期和请假记录"),
    ("eb_tool_leave_apply", "助手可发起请假"),
    ("eb_tool_approval", "助手可查待我审批"),
    ("eb_tool_duties", "助手可查岗位职责"),
)
FIELDS = tuple(field for field, _ in TOOLS)


def backfill():
    for field in FIELDS:
        if frappe.db.has_column("Designation", field):
            frappe.db.sql(f"update `tabDesignation` set `{field}`=1 where `{field}` is null")


def configure():
    backfill()  # 已有可空列先补齐，再让原生 schema 同步为 Check 的非空列。
    fields = [
        dict(
            fieldname="eb_assistant_tools_section",
            label="企业微信助手",
            fieldtype="Section Break",
            insert_after="eb_responsibilities",
        )
    ]
    previous = "eb_assistant_tools_section"
    for field, label in TOOLS:
        fields.append(
            dict(
                fieldname=field,
                label=label,
                fieldtype="Check",
                default="1",
                insert_after=previous,
                read_only_depends_on=(
                    "eval:frappe.session.user !== 'Administrator' && "
                    "!frappe.user_roles.includes('HR Manager')"
                ),
            )
        )
        previous = field
    create_custom_fields({"Designation": fields})


def validate(doc, method=None):
    if frappe.session.user == "Administrator" or "HR Manager" in frappe.get_roles():
        return
    old = doc.get_doc_before_save()
    if any(cint(doc.get(field)) != (cint(old.get(field)) if old else 1) for field in FIELDS):
        frappe.throw("岗位助手功能开关只能由 HR Manager 修改。", frappe.PermissionError)
