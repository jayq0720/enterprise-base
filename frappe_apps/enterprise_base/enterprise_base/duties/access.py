"""API 集成身份的允许列表，阻断 Employee 整条记录和其它通用方法旁路。"""

import json
from urllib.parse import unquote

import frappe

READABLE = {
    "Employee",
    "Department",
    "Designation",
    "Wiki Document",
    "Wiki Space",
    "EB Knowledge Space",
}


def restrict_integration() -> None:
    if frappe.session.user == "Administrator" or "EB Integration" not in frappe.get_roles():
        return
    path = unquote(frappe.request.path).rstrip("/")
    allowed = frappe.request.method == "GET"
    if path == "/api/method/enterprise_base.duties.api.knowledge_spaces":
        allowed = allowed and not frappe.form_dict.get("cmd")
    else:
        parts = path.split("/")
        allowed = allowed and len(parts) in (4, 5) and parts[:3] == ["", "api", "resource"]
        allowed = allowed and parts[3] in READABLE and not frappe.form_dict.get("cmd")
        allowed = allowed and not (
            set(frappe.form_dict)
            - {"fields", "filters", "limit_page_length", "limit_start", "order_by"}
        )
        if allowed and parts[3] == "Employee":
            try:
                fields = json.loads(frappe.form_dict.get("fields") or "[]")
                filters = json.loads(frappe.form_dict.get("filters") or "[]")
            except (TypeError, ValueError):
                fields, filters = [], []
            allowed = len(parts) == 4 and bool(fields) and isinstance(fields, list)
            allowed = allowed and all(
                field in ("name", "department", "designation") for field in fields
            )
            allowed = (
                allowed
                and isinstance(filters, list)
                and all(
                    isinstance(f, list)
                    and len(f) == 3
                    and f[0] in ("wecom_userid", "status")
                    and f[1] == "="
                    and isinstance(f[2], str)
                    for f in filters
                )
                and not frappe.form_dict.get("order_by")
            )
    if not allowed:
        frappe.throw("集成身份仅允许约定的只读接口和字段。", frappe.PermissionError)
