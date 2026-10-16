"""可重放的初始化；配置经 export_configuration 导出，密钥仅从部署环境注入。"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def ensure_role() -> None:
    if not frappe.db.exists("Role", "EB Integration"):
        frappe.get_doc(
            {"doctype": "Role", "role_name": "EB Integration", "desk_access": 0}
        ).insert()


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
