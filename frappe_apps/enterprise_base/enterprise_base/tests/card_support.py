"""仅临时测试库：使用 App 原生配置，只创建合成人员和业务数据。"""

import json
from pathlib import Path

import frappe


def records():
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
