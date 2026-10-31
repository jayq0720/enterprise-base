"""邮件询盘：Frappe 后台“邮件账户”勾选“附加到 Lead”后，收到的新邮件由 Frappe 原生建线索；
这里在邮件记录建好后补上渠道、需求原文，重新评分并按负载分配。

只处理刚由这封邮件建出来的询盘（还没有渠道、10 分钟内新建）；客户回复已有询盘的邮件
留在原询盘的时间线里，不改字段。邮件正文只存去掉 HTML 的纯文本。
"""

import re

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime, strip_html

from enterprise_base.marketing import scoring
from enterprise_base.marketing.inquiry import pick_owner

MAX_REQUIREMENT = 4000


def plain(html):
    """HTML 邮件转纯文本：段落、换行保留成换行，再去标签。"""
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", html)
    lines = (line.strip() for line in strip_html(html).splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def on_communication(doc, method=None):
    if (
        doc.reference_doctype != "Lead"
        or not doc.reference_name
        or doc.communication_medium != "Email"
        or doc.sent_or_received != "Received"
    ):
        return
    lead = frappe.get_doc("Lead", doc.reference_name)
    if lead.get("eb_channel") or get_datetime(lead.creation) < add_to_date(
        now_datetime(), minutes=-10
    ):
        return
    text = plain(doc.content or "")
    subject = (doc.subject or "").strip()
    requirement = (subject + "\n" + text if subject and subject not in text else text)[
        :MAX_REQUIREMENT
    ]
    points, grade, reasons = scoring.score(
        {
            "requirement": requirement,
            "email": lead.email_id or "",
            "company": lead.company_name or "",
            "product": lead.get("eb_product") or "",
            "quantity": lead.get("eb_quantity") or "",
            "timeline": lead.get("eb_timeline") or "",
            "phone": lead.mobile_no or lead.phone or "",
            "website": lead.website or "",
        }
    )
    owner = pick_owner() if grade != "无效" else None
    lead.db_set(
        {
            "eb_channel": "邮件",
            "eb_requirement": requirement,
            "eb_score": points,
            "eb_grade": grade,
            "eb_score_reasons": "；".join(reasons),
            # 邮件账户拉信时的当前用户是 Administrator，不能当负责人
            "lead_owner": owner,
            "country": None,
        },
        update_modified=False,
    )
    frappe.logger("marketing").info(
        "inquiry=%s channel=邮件 grade=%s assigned=%s", lead.name, grade, bool(owner)
    )
    if owner:
        from enterprise_base.marketing.alerts import assigned

        assigned(lead.name)
