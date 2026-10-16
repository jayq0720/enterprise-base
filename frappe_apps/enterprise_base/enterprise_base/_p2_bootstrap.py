"""临时测试站点配置生成器：真实导出后删除，以导出快照交付。"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.permissions import rights, setup_custom_perms


def rule(dt, role, level=0, **grants):
    filters = {"parent": dt, "role": role, "permlevel": level, "if_owner": 0}
    name = frappe.db.get_value("Custom DocPerm", filters)
    doc = frappe.get_doc("Custom DocPerm", name) if name else frappe.new_doc("Custom DocPerm")
    doc.update(filters)
    doc.update({right: grants.get(right, 0) for right in rights})
    doc.save()


def configure():
    if not frappe.db.exists("Client Script", "EB Employee Self Fields"):
        frappe.get_doc(
            {
                "doctype": "Client Script",
                "name": "EB Employee Self Fields",
                "dt": "Employee",
                "view": "Form",
                "enabled": 1,
                "script": """frappe.ui.form.on('Employee', {refresh(frm) {
  if (frm.doc.user_id === frappe.session.user && !frappe.user_roles.includes('HR Manager')) {
    frm.fields.filter(f => f.df.permlevel === 1).forEach(f => {
      frm.set_df_property(f.df.fieldname, 'permlevel', 0);
      frm.set_df_property(f.df.fieldname, 'read_only', 1);
    });
  }
}});""",
            }
        ).insert()
    for dt in (
        "Employee",
        "Designation",
        "Wiki Document",
        "EB Knowledge Space",
        "Leave Type",
        "Leave Application",
    ):
        setup_custom_perms(dt)
    create_custom_fields(
        {
            "Employee": [
                {
                    "fieldname": "eb_national_id",
                    "label": "身份证号",
                    "fieldtype": "Data",
                    "permlevel": 1,
                }
            ]
        }
    )
    for field in (
        "cell_number",
        "emergency_phone_number",
        "passport_number",
        "eb_national_id",
        "bank_name",
        "bank_ac_no",
        "iban",
        "salary_mode",
        "salary_currency",
        "ctc",
        "payroll_cost_center",
    ):
        make_property_setter(
            "Employee", field, "permlevel", 1, "Int", validate_fields_for_doctype=False
        )
    frappe.db.delete("Custom DocPerm", {"parent": "Employee", "permlevel": 1})
    rule("Employee", "HR Manager", 1, read=1, write=1)
    make_property_setter(
        "Designation",
        "eb_knowledge_spaces",
        "permlevel",
        1,
        "Int",
        validate_fields_for_doctype=False,
    )
    rule("Designation", "HR Manager", 1, read=1, write=1)
    frappe.db.delete("Custom DocPerm", {"parent": "EB Knowledge Space"})
    rule("EB Knowledge Space", "HR Manager", read=1, write=1, create=1, delete=1)
    rule("EB Knowledge Space", "HR User", read=1)
    rule("EB Knowledge Space", "EB Integration", read=1)
    rule("Wiki Document", "HR User", read=1, write=1, create=1)
    rule("Wiki Document", "HR Manager", read=1, write=1, create=1, delete=1)
    for dt in ("Employee", "Leave Type", "Leave Application"):
        rule(dt, "EB X4", read=1, create=int(dt == "Leave Application"))
    for name in ("EB Draft", "EB Review", "EB Published"):
        if not frappe.db.exists("Workflow State", name):
            frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": name}).insert()
    for name in ("EB Send for Review", "EB Publish", "EB Return", "EB Withdraw"):
        if not frappe.db.exists("Workflow Action Master", name):
            frappe.get_doc(
                {"doctype": "Workflow Action Master", "workflow_action_name": name}
            ).insert()
    if not frappe.db.exists("Workflow", "EB Wiki Review"):
        frappe.get_doc(
            {
                "doctype": "Workflow",
                "workflow_name": "EB Wiki Review",
                "document_type": "Wiki Document",
                "is_active": 1,
                "workflow_state_field": "eb_review_state",
                "send_email_alert": 0,
                "states": [
                    {
                        "state": state,
                        "doc_status": "0",
                        "allow_edit": role,
                        "update_field": "is_published",
                        "update_value": value,
                    }
                    for state, role, value in [
                        ("EB Draft", "HR User", "0"),
                        ("EB Review", "HR Manager", "0"),
                        ("EB Published", "HR Manager", "1"),
                    ]
                ],
                "transitions": [
                    {
                        "state": state,
                        "action": action,
                        "next_state": target,
                        "allowed": role,
                        "allow_self_approval": 1,
                    }
                    for state, action, target, role in [
                        ("EB Draft", "EB Send for Review", "EB Review", "HR User"),
                        ("EB Draft", "EB Send for Review", "EB Review", "HR Manager"),
                        ("EB Review", "EB Publish", "EB Published", "HR Manager"),
                        ("EB Review", "EB Return", "EB Draft", "HR Manager"),
                        ("EB Published", "EB Withdraw", "EB Draft", "HR Manager"),
                    ]
                ],
            }
        ).insert()
    for dt in (
        "Employee",
        "Designation",
        "Wiki Document",
        "EB Knowledge Space",
        "Leave Type",
        "Leave Application",
    ):
        frappe.clear_cache(doctype=dt)
