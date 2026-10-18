"""按公司/岗位配置触发 HRMS 原生入职单和任务，不补造招聘记录。"""

import frappe
from frappe.permissions import add_permission, setup_custom_perms, update_permission_property
from frappe.utils import add_days, getdate
from hrms.controllers.employee_boarding_controller import get_onboarding_details


def configure():
    dt = "Employee Onboarding Template"
    setup_custom_perms(dt)
    add_permission(dt, "HR Manager", ptype="read")
    for right in ("read", "write", "create", "select"):
        update_permission_property(dt, "HR Manager", 0, right, 1)
    frappe.clear_cache(doctype=dt)
    setup_custom_perms("Task")
    own = dict(parent="Task", role="HR Manager", permlevel=0, if_owner=1)
    if not frappe.db.exists("Custom DocPerm", own):
        frappe.get_doc(
            dict(
                doctype="Custom DocPerm",
                parenttype="DocType",
                parentfield="permissions",
                **own,
                read=1,
                share=1,
            )
        ).insert()
    frappe.clear_cache(doctype="Task")


def generate(doc, method=None):
    if doc.status != "Active" or not doc.designation:
        return
    window = frappe.conf.get("eb_onboarding_window_days", 30)
    if not isinstance(window, int) or not 1 <= window <= 365:
        return pending("请管理员将入职自动生成天数配置为 1 至 365 的整数。")
    if doc.date_of_joining and getdate(doc.date_of_joining) < add_days(getdate(), -window):
        return
    templates = frappe.get_all(
        "Employee Onboarding Template",
        filters={"company": doc.company, "designation": doc.designation},
        fields=["name", "department"],
    )
    templates = [t for t in templates if not t.department or t.department == doc.department]
    if not templates:
        return
    if len(templates) != 1:
        return pending("同公司岗位匹配多个入职模板，请 HR 合并后保存员工档案重试。")
    if frappe.session.user != "Administrator" and not set(frappe.get_roles()) & {
        "HR Manager",
        "System Manager",
    }:
        return pending("自动入职待办需 HR Manager 保存员工档案触发，未提升当前用户权限。")
    # 与员工保存处于同一事务；串行化重复事件，防止两个请求同时生成。
    frappe.db.sql("select name from `tabEmployee` where name=%s for update", doc.name)
    if frappe.db.exists("Employee Onboarding", {"employee": doc.name, "docstatus": ["!=", 2]}):
        return
    if not doc.job_applicant:
        return pending("请 HR 补齐员工的真实应聘者及已提交录用通知，再保存员工档案。")
    if frappe.db.count("Employee", {"job_applicant": doc.job_applicant}) != 1:
        return pending("招聘身份对应多份员工档案，请 HR 核对后再生成入职待办。")
    if frappe.db.exists(
        "Employee Onboarding", {"job_applicant": doc.job_applicant, "docstatus": ["!=", 2]}
    ):
        return pending("应聘者已有入职单，请 HR 处理原单，未创建重复待办。")
    offers = frappe.get_all(
        "Job Offer",
        filters={
            "job_applicant": doc.job_applicant,
            "company": doc.company,
            "designation": doc.designation,
            "docstatus": 1,
        },
        pluck="name",
    )
    if len(offers) != 1:
        return pending("请 HR 保留唯一、同公司且已提交的真实录用通知，再保存员工档案。")
    template = frappe.get_doc("Employee Onboarding Template", templates[0].name)
    template.check_permission("read")
    activities = get_onboarding_details(template.name, template.doctype)
    if not activities or any(
        not a.user
        or a.role
        or a.required_for_employee_creation
        or not frappe.db.get_value("User", a.user, "enabled")
        for a in activities
    ):
        return pending("请 HR 为每项活动指定启用负责人、清空角色并关闭建档前必做，再保存员工档案。")
    onboarding = frappe.get_doc(
        dict(
            doctype="Employee Onboarding",
            employee=doc.name,
            employee_name=doc.employee_name,
            job_applicant=doc.job_applicant,
            job_offer=offers[0],
            company=doc.company,
            department=doc.department,
            designation=doc.designation,
            date_of_joining=doc.date_of_joining,
            boarding_begins_on=doc.date_of_joining,
            employee_onboarding_template=template.name,
            notify_users_by_email=0,
            activities=activities,
        )
    )
    messages = list(getattr(frappe.local, "message_log", []) or [])
    frappe.db.savepoint("eb_onboarding")
    try:
        onboarding.insert()
        onboarding.submit()
    except Exception:
        frappe.db.rollback(save_point="eb_onboarding")
        frappe.local.message_log = messages
        pending("入职单未生成，请 HR 核对假日表和招聘配置后保存员工档案重试。")


def pending(message):
    # 固定提示，不输出员工内容、招聘身份、模板正文或原始异常。
    frappe.msgprint(message, title="入职待办尚未生成", indicator="orange")
