"""可重放的初始化；配置经 export_configuration 导出，密钥仅从部署环境注入。"""

import json
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, rights, setup_custom_perms

from enterprise_base.duties.access import READABLE


def ensure_role() -> None:
    if not frappe.db.exists("Role", "EB Integration"):
        frappe.get_doc(
            {"doctype": "Role", "role_name": "EB Integration", "desk_access": 0}
        ).insert()
    for doctype in READABLE - {"EB Knowledge Space"}:
        setup_custom_perms(doctype)  # 保留各标准角色权限，避免只导入集成角色而覆盖其它角色。


def configure() -> None:
    ensure_role()
    create_custom_fields(
        {
            "Employee": [
                {
                    "fieldname": "wecom_userid",
                    "label": "企业微信 userid",
                    "fieldtype": "Data",
                    "unique": 1,
                },
            ],
            "Designation": [
                {
                    "fieldname": "eb_responsibilities",
                    "label": "岗位职责",
                    "fieldtype": "Text Editor",
                },
                {
                    "fieldname": "eb_knowledge_spaces",
                    "label": "可见的知识空间",
                    "fieldtype": "Table",
                    "options": "EB Designation Space",
                },
            ],
        }
    )
    for doctype in READABLE - {"EB Knowledge Space"}:
        filters = {"parent": doctype, "role": "EB Integration", "permlevel": 0, "if_owner": 0}
        if not frappe.db.exists("Custom DocPerm", filters):
            add_permission(doctype, "EB Integration", ptype="read")
        permission = frappe.get_doc(
            "Custom DocPerm", frappe.db.get_value("Custom DocPerm", filters)
        )
        permission.update({right: int(right == "read") for right in rights})
        permission.save()
        frappe.clear_cache(doctype=doctype)
    initial = Path(frappe.get_app_path("enterprise_base", "setup_fixtures", "user.json"))
    users = (
        json.loads(initial.read_text())
        if initial.exists()
        else [
            {
                "doctype": "User",
                "email": f"eb-{label}@integration.invalid",
                "first_name": label.upper(),
                "enabled": 0,
                "send_welcome_email": 0,
                "user_type": "System User",
                "roles": [{"role": "EB Integration"}],
            }
            for label in ("x3", "x5")
        ]
    )
    for user in users:
        if not frappe.db.exists("User", user["email"]):
            doc = frappe.get_doc(user).insert()
            # Wiki 的 after_insert 会自动加 Wiki User；技术身份不继承该额外角色。
            doc.set("roles", [{"role": "EB Integration"}])
            doc.save()
