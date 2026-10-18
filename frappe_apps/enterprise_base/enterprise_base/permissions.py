"""P2 权限补齐：仅使用 App 扩展点，字段/角色/流程由原生导出配置管理。"""

import json
from urllib.parse import unquote

import frappe
from frappe.sessions import clear_sessions
from hrms.overrides.employee_master import EmployeeMaster

PRIVILEGED = {"HR User", "HR Manager", "EB Integration", "EB X4"}
# X4 只能按“=”精确查这几张表的这几个字段（只查列表，不取整条记录）
X4_COLUMNS = {
    "Employee": {"name", "employee_name", "user_id", "wecom_userid", "designation"},
    "Designation": {"name", "eb_responsibilities"},
}
X4_METHODS = {
    "hrms.hr.doctype.leave_application.leave_application.get_leave_details",
    "hrms.hr.doctype.leave_application.leave_application.get_leave_approver",
}


class Employee(EmployeeMaster):
    def update_user_permissions(self):
        super().update_user_permissions()
        if self.user_id:
            hr_scope(frappe.get_doc("User", self.user_id))

    def validate(self):
        old = self.get_doc_before_save()
        changed = any(
            (self.get(f) or "") != ((old.get(f) or "") if old else "")
            for f in ("user_id", "wecom_userid", "eb_hr_approver")
        )
        manager = frappe.session.user == "Administrator" or "HR Manager" in frappe.get_roles()
        if changed and not manager:
            frappe.throw("员工身份关联及 HR 审批人只能由 HR Manager 维护。", frappe.PermissionError)
        super().validate()

    def as_dict(self, *args, **kwargs):
        data = super().as_dict(*args, **kwargs)
        if frappe.session.user != "Administrator" and 1 not in self.get_permlevel_access("read"):
            for field in self.meta.fields:
                if field.permlevel == 1:
                    data.pop(field.fieldname, None)
        return data

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


def hr_scope(doc, method=None):
    # 只删 HRMS 按员工档案自动生成的“只能看自己”那一条；管理员手工加的范围限制保留。
    if any(r.role in {"HR User", "HR Manager"} for r in doc.roles):
        own = frappe.get_all("Employee", filters={"user_id": doc.name}, pluck="name")
        if not own:
            return
        for name in frappe.get_all(
            "User Permission",
            filters={"user": doc.name, "allow": "Employee", "for_value": ["in", own]},
            pluck="name",
        ):
            frappe.delete_doc("User Permission", name, ignore_permissions=True)


def align_wiki_review_state() -> None:
    """启用审核流程前已发布的页面补成“已发布”，其余空状态补成“草稿”。

    不补的话旧页面发布标记和审核状态对不上，wiki_review 会拒绝所有修改，
    只能先送审（会先撤下页面）。只改审核状态，不动发布标记，不触发 X5 推送；
    状态一致的页面不受影响，可重复执行。
    """
    if not frappe.db.has_column("Wiki Document", "eb_review_state"):
        return
    frappe.db.sql(
        """update `tabWiki Document` set eb_review_state = 'EB Published'
        where is_group = 0 and is_published = 1
        and ifnull(eb_review_state, '') != 'EB Published'"""
    )
    frappe.db.sql(
        """update `tabWiki Document` set eb_review_state = 'EB Draft'
        where is_group = 0 and is_published = 0 and ifnull(eb_review_state, '') = ''"""
    )


def disable_left_user(doc, method=None):
    if doc.status == "Left" and doc.user_id:
        user = frappe.get_doc("User", doc.user_id)
        if user.enabled:
            user.enabled = 0
            user.save(ignore_permissions=True)
        clear_sessions(user=doc.user_id, force=True)


def redact_version(doc, method=None):
    if doc.ref_doctype == "Employee" and doc.data:
        fields = {f.fieldname for f in frappe.get_meta("Employee").fields if f.permlevel == 1}
        data = json.loads(doc.data)
        data["changed"] = [
            [r[0], "[已隐藏]", "[已隐藏]"] if r[0] in fields else r for r in data.get("changed", [])
        ]
        doc.data = frappe.as_json(data)


def wiki_review(doc, method=None):
    manager = frappe.session.user == "Administrator" or "HR Manager" in frappe.get_roles()
    if doc.is_group and manager:
        return
    old = doc.get_doc_before_save()
    published = frappe.utils.cint(doc.is_published)
    if not manager and (published or (old and old.get("eb_review_state") != "EB Draft")):
        frappe.throw("制度必须由 HR Manager 审核发布。", frappe.PermissionError)
    if bool(published) != (doc.get("eb_review_state") == "EB Published"):
        frappe.throw("请使用制度审核流程发布或撤回，不能直接修改发布标记。", frappe.PermissionError)


def x4_draft(doc, method=None):
    if frappe.session.user != "Administrator" and "EB X4" in frappe.get_roles():
        if not doc.is_new() or doc.docstatus or doc.status != "Open":
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
        allowed = method == "POST" and dt == "Leave Application" and len(parts) == 4
        if method == "GET" and dt in {"Employee", "Designation", "Leave Type", "Leave Application"}:
            try:
                fields = json.loads(form.get("fields") or '["name"]')
                filters = json.loads(form.get("filters") or "[]")
            except (TypeError, ValueError):
                fields, filters = [], []
            columns = X4_COLUMNS.get(dt) or set(frappe.get_meta(dt).get_valid_columns())
            allowed = (
                isinstance(fields, list)
                and bool(fields)
                and all(isinstance(f, str) and f in columns for f in fields)
            )
            allowed = (
                allowed
                and isinstance(filters, list)
                and all(
                    isinstance(f, list)
                    and len(f) == 3
                    and isinstance(f[0], str)
                    and f[0] in (columns | {"status"})
                    and (dt not in X4_COLUMNS or (f[1] == "=" and isinstance(f[2], str)))
                    for f in filters
                )
            )
            order = str(form.get("order_by") or "name asc").split()
            allowed = (
                allowed
                and len(order) == 2
                and order[0] in columns
                and order[1].lower() in {"asc", "desc"}
            )
            if dt in X4_COLUMNS:
                allowed = (
                    allowed
                    and len(parts) == 4
                    and bool(form.get("fields"))
                    and not form.get("order_by")
                )
            if dt == "Designation":
                # 只允许按非空岗位名精确读取，空过滤不能退化为枚举全部职责。
                allowed = (
                    allowed
                    and bool(filters)
                    and all(f[0] == "name" and bool(f[2].strip()) for f in filters)
                )
            allowed = allowed and not (
                set(form) - {"fields", "filters", "limit_page_length", "limit_start", "order_by"}
            )
    if not allowed or form.get("cmd"):
        frappe.throw("X4 接口账号无权执行此操作。", frappe.PermissionError)
