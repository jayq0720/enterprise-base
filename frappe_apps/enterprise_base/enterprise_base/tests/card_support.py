"""仅临时测试库：装载其它独立 PR 的配置契约，不操作真实企业。"""

import json
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def configure():
    data = json.loads(Path(__file__).with_name("card_flow_data.json").read_text())
    if not frappe.db.exists("DocType", "EB Approval Card"):
        frappe.get_doc(data["schema"]).insert()
    create_custom_fields(
        {
            dt: [dict(fieldname="eb_hr_approver", label="HR", fieldtype="Link", options="User")]
            for dt in ("Employee", "Leave Application")
        }
    )
    flow = data["flow"]
    for dt, key, field, rows in (
        ("Workflow State", "state", "workflow_state_name", flow["states"]),
        ("Workflow Action Master", "action", "workflow_action_name", flow["transitions"]),
    ):
        for value in {r[key] for r in rows}:
            if not frappe.db.exists(dt, value):
                frappe.get_doc(dict(doctype=dt, **{field: value})).insert()
    if not frappe.db.exists("Workflow", flow["name"]):
        frappe.get_doc(flow).insert()


def records():
    configure()
    data = json.loads(Path(__file__).with_name("card_flow_data.json").read_text())
    for row in data["records"]:
        if not frappe.db.exists(row["doctype"], row.get("name")):
            frappe.get_doc(row).insert()
    tag, users = frappe.generate_hash(length=8), {}
    for label, roles in data["users"].items():
        email = f"card-{label}-{tag}@test.invalid"
        frappe.get_doc(
            {**data["user"], "email": email, "roles": [dict(role=r) for r in roles]}
        ).insert()
        users[label] = email
    employees = {}
    for label in users:
        employees[label] = frappe.get_doc(
            {
                **data["employee"],
                "first_name": label,
                "employee_number": tag + label,
                "wecom_userid": tag + label,
                "user_id": users[label],
                "leave_approver": users["manager"],
                "eb_hr_approver": users["hr"],
            }
        ).insert()
    return data, users, employees
