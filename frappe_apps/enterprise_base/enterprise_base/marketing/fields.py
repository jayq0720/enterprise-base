"""询盘字段定义（不 import Frappe，hooks.py 导出 fixtures 时也要用）。"""

CHANNELS = ("网站表单", "邮件", "电话", "在线聊天", "B2B 平台", "展会", "社媒", "其他")
GRADES = ("A", "B", "C", "无效")


def lead_fields():
    fields = [
        dict(
            fieldname="eb_inquiry_section",
            label="询盘",
            fieldtype="Section Break",
            insert_after="status",
            collapsible=0,
        ),
        dict(
            fieldname="eb_channel",
            label="询盘渠道",
            fieldtype="Select",
            options="\n" + "\n".join(CHANNELS),
            in_standard_filter=1,
        ),
        dict(
            fieldname="eb_grade",
            label="询盘等级",
            fieldtype="Select",
            options="\n" + "\n".join(GRADES),
            in_list_view=1,
            in_standard_filter=1,
        ),
        dict(fieldname="eb_score", label="评分", fieldtype="Int", read_only=1),
        dict(
            fieldname="eb_first_response_at",
            label="首次响应时间",
            fieldtype="Datetime",
            read_only=1,
        ),
        dict(fieldname="eb_inquiry_col", fieldtype="Column Break"),
        dict(fieldname="eb_product", label="意向产品", fieldtype="Data"),
        dict(fieldname="eb_quantity", label="数量", fieldtype="Data"),
        dict(fieldname="eb_timeline", label="时间要求", fieldtype="Data"),
        dict(fieldname="eb_requirement_section", fieldtype="Section Break"),
        dict(fieldname="eb_requirement", label="需求原文", fieldtype="Small Text"),
        dict(fieldname="eb_score_reasons", label="评分理由", fieldtype="Small Text", read_only=1),
        dict(
            fieldname="eb_attribution_section",
            label="来源归因",
            fieldtype="Section Break",
            collapsible=1,
        ),
        dict(fieldname="eb_landing_page", label="落地页", fieldtype="Data"),
        dict(fieldname="eb_referrer", label="引荐来源", fieldtype="Data"),
        dict(fieldname="eb_keyword", label="搜索关键词", fieldtype="Data"),
        dict(
            fieldname="eb_ai_engine",
            label="AI 引擎来源",
            fieldtype="Data",
            read_only=1,
            in_standard_filter=1,
        ),
        dict(fieldname="eb_attribution_col", fieldtype="Column Break"),
        dict(fieldname="eb_utm_source", label="UTM 来源", fieldtype="Data"),
        dict(fieldname="eb_utm_medium", label="UTM 媒介", fieldtype="Data"),
        dict(fieldname="eb_utm_campaign", label="UTM 活动", fieldtype="Data"),
    ]
    previous = "eb_inquiry_section"
    for field in fields[1:]:
        field["insert_after"] = previous
        previous = field["fieldname"]
    return fields
