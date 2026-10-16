"""可重放的初始化；配置经 export_configuration 导出，密钥仅从部署环境注入。"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, setup_custom_perms

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
    for label in ("x3", "x5"):
        email = f"eb-{label}@integration.invalid"
        if not frappe.db.exists("User", email):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": email,
                    "first_name": label.upper(),
                    "enabled": 0,
                    "send_welcome_email": 0,
                    "user_type": "System User",
                    "roles": [{"role": "EB Integration"}],
                }
            ).insert()
