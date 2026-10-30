"""询盘评分和 AI 来源识别：纯规则，不 import Frappe，方便单独测试和日后调整。

评分只看询盘本身能看出的信号（需求是否具体、是否企业客户、联系方式是否齐全），
结果给出分数、A/B/C/无效等级和中文理由，理由会写进询盘，销售能看到为什么是这个等级。
"""

import re
from urllib.parse import urlparse

# 常见个人邮箱域名：企业域名的邮箱加分，这些不加
FREE_MAIL = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "icloud.com",
    "aol.com",
    "mail.ru",
    "yandex.ru",
    "qq.com",
    "foxmail.com",
    "163.com",
    "126.com",
    "sina.com",
    "sohu.com",
    "aliyun.com",
}
# 推销、垃圾询盘里常见的词，命中即判无效（不分大小写）
SPAM_WORDS = (
    "seo service",
    "backlink",
    "guest post",
    "casino",
    "loan offer",
    "crypto",
    "bitcoin",
    "web design service",
    "网站优化",
    "代运营",
    "刷单",
    "贷款",
    "博彩",
)
# 引荐来源域名 → AI 引擎名称（GEO 归因用）
AI_ENGINES = {
    "chatgpt.com": "ChatGPT",
    "chat.openai.com": "ChatGPT",
    "perplexity.ai": "Perplexity",
    "gemini.google.com": "Gemini",
    "copilot.microsoft.com": "Copilot",
    "claude.ai": "Claude",
    "doubao.com": "豆包",
    "kimi.com": "Kimi",
    "kimi.moonshot.cn": "Kimi",
    "chat.deepseek.com": "DeepSeek",
    "yuanbao.tencent.com": "元宝",
    "tongyi.aliyun.com": "通义",
    "qianwen.aliyun.com": "通义",
}
_QUANTITY = re.compile(
    r"\d+\s*(pcs|pieces|units|sets|tons?|kg|moq|台|套|件|个|吨|箱|只|张|米)", re.I
)
_URL = re.compile(r"https?://", re.I)
GRADE_A, GRADE_B = 60, 35


def ai_engine(referrer: str = "", utm_source: str = "") -> str:
    """从引荐地址或 utm_source 认出 AI 引擎，认不出返回空。"""
    host = (urlparse(referrer).hostname or "") if referrer else ""
    for candidate in (host, (utm_source or "").strip().lower()):
        candidate = candidate.removeprefix("www.")
        for domain, name in AI_ENGINES.items():
            if candidate == domain or candidate.endswith("." + domain):
                return name
        for name in set(AI_ENGINES.values()):
            if candidate and candidate == name.lower():
                return name
    return ""


def score(inquiry: dict[str, str]) -> tuple[int, str, list[str]]:
    """返回（分数，等级，理由）。等级是 A、B、C 或“无效”。"""
    text = (inquiry.get("requirement") or "").strip()
    lowered = text.lower()
    email = (inquiry.get("email") or "").strip().lower()
    company = (inquiry.get("company") or "").strip()
    if not text and not company:
        return 0, "无效", ["没有需求内容也没有公司名"]
    if hit := next((w for w in SPAM_WORDS if w in lowered), None):
        return 0, "无效", [f"疑似推销或垃圾信息（含“{hit}”）"]
    if len(_URL.findall(text)) > 2:
        return 0, "无效", ["需求里链接过多，疑似垃圾信息"]

    points, reasons = 0, []

    def add(value: int, reason: str) -> None:
        nonlocal points
        points += value
        reasons.append(f"{reason} +{value}")

    domain = email.rpartition("@")[2]
    if domain and domain not in FREE_MAIL:
        add(20, "企业邮箱")
    if company:
        add(15, "留了公司名")
    if len(text) >= 50:
        add(15, "需求描述具体")
    elif len(text) >= 20:
        add(8, "有需求描述")
    if (inquiry.get("product") or "").strip():
        add(10, "指明了产品")
    if (inquiry.get("quantity") or "").strip() or _QUANTITY.search(text):
        add(15, "有数量")
    if (inquiry.get("timeline") or "").strip():
        add(10, "有时间要求")
    if (inquiry.get("phone") or "").strip():
        add(10, "留了电话")
    if (inquiry.get("website") or "").strip():
        add(5, "留了官网")
    grade = "A" if points >= GRADE_A else "B" if points >= GRADE_B else "C"
    return points, grade, reasons
