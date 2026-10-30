"""询盘中心：用 ERPNext 原生“线索（Lead）”存询盘，只加字段和钩子，不改上游代码。

- 字段：渠道、需求原文、意向产品/数量/时间、来源归因（落地页、引荐、UTM、关键词、AI 引擎）、
  评分和首次响应时间
- 岗位开关：“处理询盘”“查询盘统计”，默认关闭，只有 HR Manager 能改
- 新建时按规则评分、认出 AI 来源；状态第一次离开“Lead”时记首次响应时间
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, now_datetime

from enterprise_base.marketing import scoring
from enterprise_base.marketing.fields import lead_fields

# 还在跟进中的状态：分配时按这些算各人手上的量，“我的询盘”默认也只列这些
OPEN_STATUSES = ("Lead", "Open", "Replied", "Interested")
SWITCHES = (
    ("eb_tool_inquiry", "助手可处理分配给我的询盘"),
    ("eb_tool_inquiry_stats", "助手可查询盘统计"),
)
SWITCH_FIELDS = tuple(field for field, _ in SWITCHES)
MANAGER_ONLY = (
    "eval:frappe.session.user !== 'Administrator' && !frappe.user_roles.includes('HR Manager')"
)


def configure():
    designation = [
        dict(
            fieldname="eb_marketing_tools_section",
            label="营销助手",
            fieldtype="Section Break",
            insert_after="eb_tool_duties",
        )
    ]
    previous = "eb_marketing_tools_section"
    for field, label in SWITCHES:
        designation.append(
            dict(
                fieldname=field,
                label=label,
                fieldtype="Check",
                default="0",
                insert_after=previous,
                read_only_depends_on=MANAGER_ONLY,
            )
        )
        previous = field
    create_custom_fields({"Lead": lead_fields(), "Designation": designation})


def validate_switches(doc, method=None):
    if frappe.session.user == "Administrator" or "HR Manager" in frappe.get_roles():
        return
    old = doc.get_doc_before_save()
    if any(cint(doc.get(f)) != (cint(old.get(f)) if old else 0) for f in SWITCH_FIELDS):
        frappe.throw("营销助手开关只能由 HR Manager 修改。", frappe.PermissionError)


def before_insert(doc, method=None):
    """新询盘：认出 AI 来源，没人工定级的按规则评分。"""
    doc.eb_ai_engine = scoring.ai_engine(
        doc.get("eb_referrer") or "", doc.get("eb_utm_source") or ""
    )
    if doc.get("eb_grade") and doc.get("eb_score"):
        return
    points, grade, reasons = scoring.score(
        {
            "requirement": doc.get("eb_requirement") or "",
            "email": doc.get("email_id") or "",
            "company": doc.get("company_name") or "",
            "product": doc.get("eb_product") or "",
            "quantity": doc.get("eb_quantity") or "",
            "timeline": doc.get("eb_timeline") or "",
            "phone": doc.get("mobile_no") or doc.get("phone") or "",
            "website": doc.get("website") or "",
        }
    )
    doc.eb_score = points
    doc.eb_grade = doc.get("eb_grade") or grade
    doc.eb_score_reasons = "；".join(reasons)


def validate(doc, method=None):
    """状态第一次离开“Lead”（新询盘）时记首次响应时间，用来算响应时效。"""
    if doc.is_new() or doc.get("eb_first_response_at"):
        return
    old = doc.get_doc_before_save()
    if old and old.status == "Lead" and doc.status != "Lead":
        doc.eb_first_response_at = now_datetime()


def handlers():
    """开通了“处理询盘”的在职员工对应的启用用户（排除接口账号）。"""
    rows = frappe.db.sql(
        """select distinct e.user_id from `tabEmployee` e
        join `tabDesignation` d on d.name = e.designation
        join `tabUser` u on u.name = e.user_id
        where e.status = 'Active' and u.enabled = 1 and u.user_type = 'System User'
        and d.eb_tool_inquiry = 1 order by e.user_id""",
        as_dict=True,
    )
    users = []
    for row in rows:
        roles = set(frappe.get_roles(row.user_id))
        if "Employee" in roles and not roles & {"EB Integration", "EB X4"}:
            users.append(row.user_id)
    return users


def pick_owner():
    """按手上未结询盘最少的人分配，一样多按用户名排序，保证结果可复现。"""
    users = handlers()
    if not users:
        return None
    load = dict.fromkeys(users, 0)
    for row in frappe.get_all(
        "Lead",
        filters={"lead_owner": ["in", users], "status": ["in", OPEN_STATUSES]},
        fields=["lead_owner", "count(name) as n"],
        group_by="lead_owner",
    ):
        load[row.lead_owner] = row.n
    return min(users, key=lambda u: (load[u], u))
