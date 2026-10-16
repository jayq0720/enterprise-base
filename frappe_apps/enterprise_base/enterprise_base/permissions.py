"""P2 权限补齐：仅使用 App 扩展点，字段/角色/流程由原生导出配置管理。"""

import json
from urllib.parse import unquote

import frappe
from frappe.sessions import clear_sessions
from hrms.overrides.employee_master import EmployeeMaster

PRIVILEGED = {"HR User", "HR Manager", "EB Integration", "EB X4"}
X4_METHODS = {
    "hrms.hr.doctype.leave_application.leave_application.get_leave_details",
    "hrms.hr.doctype.leave_application.leave_application.get_leave_approver",
}


class Employee(EmployeeMaster):
    def get_permlevel_access(self, permission_type="write"):
        levels = [p for p in super().get_permlevel_access(permission_type) if p != 1]
        roles = frappe.get_roles()
        own = self.user_id == frappe.session.user and "Employee" in roles
        if "HR Manager" in roles or (own and permission_type == "read"):
            levels.append(1)
        return levels


def employee_access(doc, ptype, user=None):
    user = user or frappe.session.user
    if user != "Administrator" and not (set(frappe.get_roles(user)) & PRIVILEGED):
        return ptype in {"read", "select", "print"} and doc.user_id == user


def employee_query(user=None):
    user = user or frappe.session.user
    if user != "Administrator" and not (set(frappe.get_roles(user)) & PRIVILEGED):
        return "`tabEmployee`.`user_id` = " + frappe.db.escape(user)
    return ""


def disable_left_user(doc, method=None):
    if doc.status == "Left" and doc.user_id:
        user = frappe.get_doc("User", doc.user_id)
        if user.enabled:
            user.enabled = 0
            user.save(ignore_permissions=True)
        clear_sessions(user=doc.user_id, force=True)


def wiki_review(doc, method=None):
    if doc.is_group:
        return
    old = doc.get_doc_before_save()
    manager = frappe.session.user == "Administrator" or "HR Manager" in frappe.get_roles()
    if not manager and (doc.is_published or (old and old.get("eb_review_state") != "EB Draft")):
        frappe.throw("制度必须由 HR Manager 审核发布。", frappe.PermissionError)
    if bool(doc.is_published) != (doc.get("eb_review_state") == "EB Published"):
        frappe.throw("请使用制度审核流程发布或撤回，不能直接修改发布标记。", frappe.PermissionError)


def x4_draft(doc, method=None):
    if "EB X4" in frappe.get_roles() and (
        not doc.is_new() or doc.docstatus or doc.status != "Open"
    ):
        frappe.throw("X4 仅能新建未提交、未审批的请假草稿。", frappe.PermissionError)


def x4_api():
    if frappe.session.user == "Administrator" or "EB X4" not in frappe.get_roles():
        return
    path = unquote(frappe.request.path).rstrip("/")
    method, form = frappe.request.method, frappe.form_dict
    parts = path.split("/")
    allowed = method == "GET" and path.removeprefix("/api/method/") in X4_METHODS
    if parts[:3] == ["", "api", "resource"] and len(parts) in (4, 5):
        dt = parts[3]
        allowed = method == "GET" and dt in {"Employee", "Leave Type", "Leave Application"}
        if dt == "Employee" and allowed:
            try:
                fields = json.loads(form.get("fields") or "[]")
            except (TypeError, ValueError):
                fields = []
            allowed = len(parts) == 4 and isinstance(fields, list) and bool(fields)
            allowed = allowed and all(
                f in {"name", "employee_name", "user_id", "wecom_userid"} for f in fields
            )
            allowed = allowed and not (
                set(form) - {"fields", "filters", "limit_page_length", "limit_start"}
            )
        allowed = allowed or (method == "POST" and dt == "Leave Application" and len(parts) == 4)
        if method == "GET":
            allowed = allowed and not (
                set(form) - {"fields", "filters", "limit_page_length", "limit_start", "order_by"}
            )
    if not allowed or form.get("cmd"):
        frappe.throw("X4 接口账号无权执行此操作。", frappe.PermissionError)
