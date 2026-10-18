"""同一企业微信应用统一入口：先验签，再分流卡片或原样转交聊天。"""

import base64
import hashlib
import importlib

import frappe
import requests
from werkzeug.exceptions import BadRequest
from werkzeug.wrappers import Response

PATH = "/api/method/enterprise_base.wecom.callback.receive"
LIMIT = 65536


def guard():
    req = frappe.request
    if req.path != PATH:
        return
    fields = {"msg_signature", "timestamp", "nonce"}
    if req.method == "GET":
        fields.add("echostr")
    if (
        req.method not in {"GET", "POST"}
        or set(req.args) != fields
        or any(len(req.args.getlist(k)) != 1 for k in fields)
        or len(req.query_string) > 70000
        or req.headers.get("Authorization")
        or req.headers.get("Cookie")
        or (
            req.method == "POST"
            and (
                req.mimetype not in {"application/xml", "text/xml"}
                or (req.content_length or 0) > LIMIT
            )
        )
    ):
        raise BadRequest("企业微信回调请求无效。")


def protocol():
    return importlib.import_module("enterprise_base.wecom.crypto")


def approve(event):
    return importlib.import_module("enterprise_base.wecom.card_actions").apply(event)


def reply(body=b"", status=200):
    return Response(
        body,
        status=status,
        content_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def forward(body, args, codec):
    # 不透传 Cookie、认证头、Host、代理设置或不受控目标地址。
    try:
        with requests.Session() as client:
            client.trust_env = False
            with client.post(
                "http://wechat:9898/wxcomapp",
                params=args,
                data=body,
                headers={"Content-Type": "application/xml"},
                timeout=(1, 3),
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    return None
                content = b""
                for chunk in response.iter_content(8192):
                    content += chunk
                    if len(content) > LIMIT:
                        return None
                if content.strip() not in {b"", b"success"}:
                    fields = codec.xml_fields(content)
                    if set(fields) != {"Encrypt", "MsgSignature", "TimeStamp", "Nonce"}:
                        return None
                return content
    except Exception:
        return None


def chat(event, body, args, codec):
    # Redis 原子占位防止同一 MsgId 并发转发；缓存只含加密回复，不含正文。
    if not event.get("MsgId"):
        result = forward(body, args, codec)
        return reply(result) if result is not None else reply(status=502)
    digest = hashlib.sha256(
        "|".join(
            event.get(k, "") for k in ("ToUserName", "AgentID", "FromUserName", "MsgId")
        ).encode()
    ).hexdigest()
    key = frappe.cache.make_key("eb-wecom-message:" + digest)
    if not frappe.cache.set(key, b"busy", ex=300, nx=True):
        cached = frappe.cache.get(key)
        return (
            reply(base64.b64decode(cached[5:]))
            if cached and cached.startswith(b"done:")
            else reply(status=503)
        )
    result = forward(body, args, codec)
    if result is None:
        # 超时不代表桥接未受理；保留占位，避免一次消息触发多次模型调用。
        return reply(status=502)
    frappe.cache.set(key, b"done:" + base64.b64encode(result), ex=300)
    return reply(result)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def receive(**_ignored):
    guard()
    cfg = frappe.conf.get("eb_wecom_cards")
    if (
        not isinstance(cfg, dict)
        or cfg.get("enabled") is not True
        or not all(
            isinstance(cfg.get(k), str) and cfg[k]
            for k in ("corpid", "agentid", "token", "aes_key")
        )
    ):
        return reply(status=503)
    try:
        codec = protocol()
    except ImportError:
        return reply(status=503)
    args = frappe.request.args.to_dict()
    try:
        body = frappe.request.get_data()
        if len(body) > LIMIT:
            return reply(status=413)
        encrypted = (
            args["echostr"] if frappe.request.method == "GET" else codec.xml_fields(body)["Encrypt"]
        )
        message = codec.decrypt(
            encrypted,
            args["msg_signature"],
            args["timestamp"],
            args["nonce"],
            token=cfg["token"],
            key=cfg["aes_key"],
            corpid=cfg["corpid"],
        )
        if frappe.request.method == "GET":
            return reply(message)
        event = codec.xml_fields(message)
        if event.get("ToUserName") != cfg["corpid"] or event.get("AgentID") != cfg["agentid"]:
            return reply(status=403)
        if event.get("Event") == "template_card_event":
            return reply(b"success") if approve(event) else reply(status=403)
        return chat(event, body, args, codec)
    except Exception:
        # 禁止 Frappe 错误快照保存原始请求或异常；审批拒绝由处理器回滚保存点。
        frappe.db.rollback()
        return reply(status=403)
