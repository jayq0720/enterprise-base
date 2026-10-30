"""X4 接口账号调用的询盘方法（员工前台和 Dify 询盘助手都经 X4 走这里）。

- intake：外部渠道进件，去重、评分、按负载分配
- my_inquiries / inquiry / follow_up：只对询盘负责人本人开放，身份是企业微信 userid，
  按审批卡片同样的规则校验（在职、启用、非接口账号）
- stats：按渠道、等级、AI 来源、状态统计，不含客户联系方式

岗位开关（处理询盘 / 查询盘统计）由 X4 在调用前检查；这里只认 X4 账号和负责人本人。
客户的邮箱、电话只有 inquiry(contact=1) 才返回，X4 只在员工前台给负责人本人用，不给模型。
"""

import json

import frappe
from frappe.utils import add_days, cint, now_datetime, strip_html

from enterprise_base.marketing.fields import CHANNELS
from enterprise_base.marketing.inquiry import OPEN_STATUSES, pick_owner
from enterprise_base.wecom.card_actions import actor

# 进件字段 → Lead 字段，以及长度上限
INTAKE = {
    "name": ("first_name", 140),
    "company": ("company_name", 140),
    "email": ("email_id", 140),
    "phone": ("mobile_no", 40),
    "website": ("website", 140),
    "product": ("eb_product", 140),
    "quantity": ("eb_quantity", 140),
    "timeline": ("eb_timeline", 140),
    "requirement": ("eb_requirement", 4000),
    "landing_page": ("eb_landing_page", 500),
    "referrer": ("eb_referrer", 500),
    "keyword": ("eb_keyword", 140),
    "utm_source": ("eb_utm_source", 140),
    "utm_medium": ("eb_utm_medium", 140),
    "utm_campaign": ("eb_utm_campaign", 140),
}
DUPLICATE_DAYS = 30
# 负责人能通过助手或前台改到的状态；转商机、成交、报价走 ERPNext 原生操作
FOLLOW_UP_STATUSES = ("Open", "Replied", "Interested", "Do Not Contact")
LIST_FIELDS = [
    "name",
    "lead_name",
    "company_name",
    "country",
    "status",
    "eb_channel",
    "eb_grade",
    "eb_score",
    "eb_product",
    "creation",
    "eb_first_response_at",
]
DETAIL_FIELDS = [
    *LIST_FIELDS,
    "language",
    "eb_quantity",
    "eb_timeline",
    "eb_requirement",
    "eb_score_reasons",
    "eb_landing_page",
    "eb_keyword",
    "eb_ai_engine",
    "eb_utm_source",
    "eb_utm_medium",
    "eb_utm_campaign",
    "website",
]
CONTACT_FIELDS = ["email_id", "mobile_no", "phone"]
MAX_NOTE = 1000


def _require_x4():
    user = frappe.session.user
    if user in {"Administrator", "Guest"} or "EB X4" not in frappe.get_roles(user):
        frappe.throw("只有 X4 接口账号可以调用。", frappe.PermissionError)


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _clean(row):
    return {
        k: (str(v) if k in ("creation", "eb_first_response_at") and v else v)
        for k, v in row.items()
    }


@frappe.whitelist(methods=["POST"])
def intake(data=None):
    """进件。data 是 JSON 对象；返回询盘号、是否重复、等级，不返回负责人。"""
    _require_x4()
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return {"ok": False, "reason": "invalid"}
    values = {field: _text(data.get(key), limit) for key, (field, limit) in INTAKE.items()}
    values["email_id"] = values["email_id"].lower()
    if values["email_id"] and not frappe.utils.validate_email_address(values["email_id"]):
        values["email_id"] = ""
    if values["mobile_no"] and not frappe.utils.validate_phone_number(values["mobile_no"]):
        values["mobile_no"] = ""
    if not (values["email_id"] or values["mobile_no"]):
        return {"ok": False, "reason": "no_contact"}
    if not (values["first_name"] or values["company_name"]):
        values["first_name"] = values["email_id"].partition("@")[0] or values["mobile_no"]
    channel = _text(data.get("channel"), 40)
    values["eb_channel"] = channel if channel in CHANNELS else "其他"

    if existing := _duplicate(values):
        return _merge(existing, values)

    doc = frappe.get_doc({"doctype": "Lead", **values, "status": "Lead"})
    given = set()
    for field, doctype in (("country", "Country"), ("language", "Language")):
        value = _text(data.get(field), 140)
        if value and frappe.db.exists(doctype, value):
            doc.set(field, value)
            given.add(field)
    try:
        doc.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        # ERPNext 默认不允许同一邮箱建两条线索（CRM 设置可改）；不改设置，并入最近那条
        frappe.clear_last_message()
        existing = frappe.get_all(
            "Lead",
            filters={"email_id": values["email_id"]},
            pluck="name",
            order_by="creation desc",
            limit=1,
        )
        if not existing:
            raise
        return _merge(existing[0], values)
    # Lead 的负责人默认是当前用户（X4 账号），这里改成分配结果；
    # 判为无效的不分给销售，留给主管在后台复核
    owner = pick_owner() if doc.eb_grade != "无效" else None
    # 国家、语言没给的不沿用系统默认值（默认国家会被当成客户所在国）
    cleared = {f: None for f in ("country", "language") if f not in given}
    doc.db_set({"lead_owner": owner, **cleared}, update_modified=False)
    frappe.logger("marketing").info(
        "inquiry=%s channel=%s grade=%s assigned=%s",
        doc.name,
        doc.eb_channel,
        doc.eb_grade,
        bool(owner),
    )
    return {
        "ok": True,
        "id": doc.name,
        "duplicate": False,
        "grade": doc.eb_grade,
        "assigned": bool(owner),
    }


def _merge(name, values):
    """重复询盘不新建，在原询盘上加一条跟进记录，负责人能看到客户又来问了。"""
    doc = frappe.get_doc("Lead", name)
    content = frappe.utils.escape_html(values["eb_requirement"] or "（无内容）")
    doc.append(
        "notes",
        {"note": f"重复询盘（{values['eb_channel']}）：{content}", "added_on": now_datetime()},
    )
    doc.save(ignore_permissions=True)
    return {"ok": True, "id": doc.name, "duplicate": True, "grade": doc.eb_grade}


def _duplicate(values):
    since = add_days(now_datetime(), -DUPLICATE_DAYS)
    for field in ("email_id", "mobile_no"):
        if values[field]:
            found = frappe.get_all(
                "Lead",
                filters={
                    field: values[field],
                    "creation": [">=", since],
                    "status": ["not in", ("Converted", "Do Not Contact")],
                },
                pluck="name",
                order_by="creation desc",
                limit=1,
            )
            if found:
                return found[0]
    return None


def _owned(userid, lead):
    """负责人本人才返回 (用户, 询盘号)，否则 (None, None)；不透露询盘是否存在。"""
    if not (isinstance(lead, str) and 0 < len(lead) <= 140):
        return None, None
    user = actor(userid) if isinstance(userid, str) and 0 < len(userid) <= 64 else None
    if not user or frappe.db.get_value("Lead", lead, "lead_owner") != user:
        return None, None
    return user, lead


@frappe.whitelist(methods=["POST"])
def my_inquiries(userid="", scope="open"):
    _require_x4()
    user = actor(userid) if isinstance(userid, str) and 0 < len(userid) <= 64 else None
    if not user:
        return {"ok": False, "reason": "forbidden"}
    filters = {"lead_owner": user}
    if scope != "all":
        filters["status"] = ["in", OPEN_STATUSES]
    rows = frappe.get_all(
        "Lead", filters=filters, fields=LIST_FIELDS, order_by="creation desc", limit=50
    )
    rank = {"A": 0, "B": 1, "C": 2}
    rows.sort(key=lambda r: rank.get(r.eb_grade or "", 3))
    return {"ok": True, "inquiries": [_clean(r) for r in rows[:20]]}


@frappe.whitelist(methods=["POST"])
def inquiry(userid="", lead="", contact=0):
    _require_x4()
    user, name = _owned(userid, lead)
    if not user:
        return {"ok": False, "reason": "forbidden"}
    fields = DETAIL_FIELDS + (CONTACT_FIELDS if cint(contact) else [])
    row = frappe.db.get_value("Lead", name, fields, as_dict=True)
    notes = frappe.get_all(
        "CRM Note",
        filters={"parenttype": "Lead", "parent": name},
        fields=["note", "added_by", "added_on"],
        order_by="added_on desc",
        limit=10,
    )
    names = {}
    for n in notes:
        if n.added_by and n.added_by not in names:
            names[n.added_by] = frappe.utils.get_fullname(n.added_by)
    return {
        "ok": True,
        "inquiry": _clean(row),
        "notes": [
            {
                "note": strip_html(n.note or "")[:MAX_NOTE],
                "by": names.get(n.added_by, ""),
                "at": str(n.added_on or ""),
            }
            for n in notes
        ],
    }


@frappe.whitelist(methods=["POST"])
def follow_up(userid="", lead="", status="", note=""):
    """负责人本人记跟进、改状态；以负责人身份保存，修改记录里是本人。"""
    _require_x4()
    status, note = _text(status, 40), _text(note, MAX_NOTE)
    if (status and status not in FOLLOW_UP_STATUSES) or not (status or note):
        return {"ok": False, "reason": "invalid"}
    caller = frappe.session.user
    frappe.db.savepoint("eb_follow_up")
    try:
        user, name = _owned(userid, lead)
        if not user:
            return {"ok": False, "reason": "forbidden"}
        frappe.db.sql("select name from `tabLead` where name=%s for update", name)
        frappe.set_user(user)
        doc = frappe.get_doc("Lead", name)
        if status:
            doc.status = status
        if note:
            doc.append(
                "notes",
                {
                    "note": frappe.utils.escape_html(note),
                    "added_by": user,
                    "added_on": now_datetime(),
                },
            )
        # 授权已由负责人校验和岗位开关完成，销售不必另有 Lead 的后台编辑权限
        doc.save(ignore_permissions=True)
        return {"ok": True, "status": doc.status}
    except Exception:
        frappe.db.rollback(save_point="eb_follow_up")
        frappe.logger("marketing").info("follow_up lead=%s result=rejected", lead)
        return {"ok": False, "reason": "rejected"}
    finally:
        frappe.set_user(caller)


@frappe.whitelist(methods=["POST"])
def stats(userid="", days=30):
    """最近 N 天新进询盘的统计。只要求调用人是有效员工，岗位开关由 X4 检查。"""
    _require_x4()
    user = actor(userid) if isinstance(userid, str) and 0 < len(userid) <= 64 else None
    if not user:
        return {"ok": False, "reason": "forbidden"}
    days = min(max(cint(days) or 30, 1), 366)
    since = add_days(now_datetime(), -days)

    def group(field, empty="（未填）"):
        rows = frappe.db.sql(
            f"select ifnull(`{field}`, '') as k, count(*) as n from `tabLead` "
            f"where creation >= %s group by k order by n desc",
            since,
            as_dict=True,
        )
        return {r.k or empty: r.n for r in rows}

    total = frappe.db.count("Lead", {"creation": [">=", since]})
    response = frappe.db.sql(
        "select avg(timestampdiff(minute, creation, eb_first_response_at)) from `tabLead` "
        "where creation >= %s and eb_first_response_at is not null",
        since,
    )[0][0]
    return {
        "ok": True,
        "days": days,
        "total": total,
        "by_channel": group("eb_channel"),
        "by_grade": group("eb_grade"),
        "by_ai_engine": group("eb_ai_engine", "非 AI 来源"),
        "by_status": group("status"),
        "avg_first_response_minutes": round(float(response), 1) if response is not None else None,
    }
