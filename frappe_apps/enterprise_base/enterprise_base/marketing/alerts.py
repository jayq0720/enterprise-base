"""询盘提醒：经企业微信应用消息提醒负责人和市场负责人。

- A 级询盘分配后立即提醒负责人
- 每小时检查：超过时限（默认 A 级 4 小时、其他 24 小时）还没首次跟进的提醒负责人；
  超过两倍时限还没跟进的，提醒开通“查询盘统计”的市场负责人
- 消息里只有询盘号、等级、产品、国家、渠道，没有客户姓名和联系方式

复用审批卡片的企业微信应用（site_config 的 eb_wecom_cards）；卡片关闭时提醒也不发。
site_config 可选 eb_marketing_alerts：enabled（默认 true）、sla_hours（按等级）、portal_url
（默认把 desk_url 的 hr. 换成 portal.）。提醒进度记在询盘的 eb_alert_level 上：
1 已发新询盘提醒、2 已提醒负责人超时、3 已提醒市场负责人。发送失败不记，下小时重试。
"""

import frappe
from frappe.utils import cint, get_datetime, now_datetime, strip_html, time_diff_in_hours

SLA_HOURS = {"A": 4, "B": 24, "C": 24}
GRADES = ("A", "B", "C")


def settings():
    cards = frappe.conf.get("eb_wecom_cards")
    if not isinstance(cards, dict) or cards.get("enabled") is not True:
        return None
    cfg = frappe.conf.get("eb_marketing_alerts")
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("enabled", True) is not True:
        return None
    hours: dict[str, float] = dict(SLA_HOURS)
    for grade, value in (cfg.get("sla_hours") or {}).items():
        if grade in hours and isinstance(value, int | float) and 0 < value <= 24 * 30:
            hours[grade] = value
    url = cfg.get("portal_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        desk = cards.get("desk_url")
        url = (
            "https://portal." + desk.removeprefix("https://hr.")
            if isinstance(desk, str) and desk.startswith("https://hr.")
            else ""
        )
    return {"sla_hours": hours, "portal_url": url.rstrip("/")}


def wecom_userid(user):
    """负责人的企业微信 userid；离职、停用、没绑企业微信的返回空，不发。"""
    rows = frappe.get_all(
        "Employee",
        filters={"user_id": user, "status": "Active"},
        fields=["wecom_userid"],
        limit_page_length=2,
    )
    if len(rows) != 1 or not rows[0].wecom_userid:
        return ""
    if not frappe.db.get_value("User", user, "enabled"):
        return ""
    return rows[0].wecom_userid


def managers():
    """开通“查询盘统计”的在职员工的企业微信 userid。"""
    rows = frappe.db.sql(
        """select distinct e.wecom_userid from `tabEmployee` e
        join `tabDesignation` d on d.name = e.designation
        join `tabUser` u on u.name = e.user_id
        where e.status = 'Active' and u.enabled = 1 and d.eb_tool_inquiry_stats = 1
        and ifnull(e.wecom_userid, '') != '' order by e.wecom_userid""",
    )
    return [r[0] for r in rows]


def cut(value, limit):
    return strip_html(str(value or "")).strip()[:limit]


def message(touser, cfg, lead, title, note):
    lines = [
        f"询盘 {lead.name} · {lead.eb_grade or '未定级'}级",
        f"产品：{cut(lead.eb_product, 60) or '（未填）'}",
        f"国家：{cut(lead.country, 40) or '（未填）'} · 渠道：{cut(lead.eb_channel, 20) or '其他'}",
        note,
    ]
    description = "\n".join(lines)
    if cfg["portal_url"]:
        return dict(
            touser=touser,
            msgtype="textcard",
            textcard=dict(
                title=title,
                description=description,
                url=cfg["portal_url"] + "/#inquiry=" + lead.name,
                btntxt="去处理",
            ),
        )
    return dict(touser=touser, msgtype="text", text=dict(content=title + "\n" + description))


def send(body):
    from enterprise_base.wecom.card_api import request

    try:
        result = request("message/send", body)
    except Exception:
        return False
    return isinstance(result, dict) and result.get("errcode", 0) == 0


LEAD_FIELDS = [
    "name",
    "lead_owner",
    "status",
    "eb_grade",
    "eb_product",
    "eb_channel",
    "country",
    "creation",
    "eb_first_response_at",
    "eb_alert_level",
]


def assigned(lead):
    """进件分配后调用：A 级询盘在事务提交后提醒负责人。"""
    if settings() and frappe.db.get_value("Lead", lead, "eb_grade") == "A":
        frappe.enqueue(
            "enterprise_base.marketing.alerts.notify_new", lead=lead, enqueue_after_commit=True
        )


def notify_new(lead):
    cfg = settings()
    row = frappe.db.get_value("Lead", lead, LEAD_FIELDS, as_dict=True)
    if not cfg or not row or cint(row.eb_alert_level) >= 1:
        return
    if row.eb_grade != "A" or row.status != "Lead" or not row.lead_owner:
        return
    _new(cfg, row)


def _new(cfg, row):
    touser = wecom_userid(row.lead_owner)
    note = f"请在 {cfg['sla_hours']['A']} 小时内首次跟进。"
    if touser and send(message(touser, cfg, row, "新的 A 级询盘", note)):
        _mark(row.name, 1)


def _mark(lead, level):
    frappe.db.set_value("Lead", lead, "eb_alert_level", level, update_modified=False)
    frappe.logger("marketing").info("inquiry=%s alert_level=%s", lead, level)


def overdue():
    """每小时：没跟进的询盘按时限提醒；状态还是“Lead”且没有首次响应时间才算没跟进。"""
    cfg = settings()
    if not cfg:
        return
    now = now_datetime()
    rows = frappe.get_all(
        "Lead",
        filters={
            "status": "Lead",
            "eb_first_response_at": ["is", "not set"],
            "eb_grade": ["in", GRADES],
            "lead_owner": ["is", "set"],
            "eb_alert_level": ["<", 3],
            "creation": [">=", frappe.utils.add_days(now, -30)],
        },
        fields=LEAD_FIELDS,
        order_by="creation asc",
        limit_page_length=500,
    )
    escalate = None
    for row in rows:
        sla = cfg["sla_hours"][row.eb_grade]
        age = time_diff_in_hours(now, get_datetime(row.creation))
        level = cint(row.eb_alert_level)
        if age >= 2 * sla:
            if escalate is None:
                escalate = managers()
            name = frappe.utils.get_fullname(row.lead_owner)
            note = f"已 {int(age)} 小时没有跟进，负责人：{cut(name, 20)}。"
            sent = [send(message(u, cfg, row, "询盘超时未跟进", note)) for u in escalate]
            if any(sent):
                _mark(row.name, 3)
        elif age >= sla and level < 2:
            touser = wecom_userid(row.lead_owner)
            note = f"已 {int(age)} 小时没有跟进，超过 {2 * sla} 小时会提醒市场负责人。"
            if touser and send(message(touser, cfg, row, "询盘待跟进", note)):
                _mark(row.name, 2)
        elif row.eb_grade == "A" and level < 1:
            _new(cfg, row)  # 新询盘提醒当时没发出去，补发
