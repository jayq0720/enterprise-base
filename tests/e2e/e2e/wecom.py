"""模拟企业微信服务器往统一回调地址推消息：按企业微信官方协议加密、签名。

只用合成的企业 ID、Token 和 EncodingAESKey，和测试环境的配置一致。
"""

import base64
import hashlib
import secrets
import struct
import time
from xml.etree import ElementTree as ET

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from e2e.settings import E2ESettings


class WeCom:
    def __init__(self, s: E2ESettings) -> None:
        assert s.wecom_callback_url and s.wecom_token and s.wecom_aes_key
        self.corp_id, self.agent_id = s.wecom_corp_id, s.wecom_agent_id
        self._token = s.wecom_token.get_secret_value()
        self._key = base64.b64decode(s.wecom_aes_key.get_secret_value() + "=")
        headers = {"Host": s.wecom_callback_host} if s.wecom_callback_host else {}
        # 企业微信的请求不带 Cookie 和认证头
        self.http = httpx.Client(headers=headers, timeout=s.http_timeout_seconds)
        self.url = str(s.wecom_callback_url)

    def text(self, userid: str, content: str, msg_id: str) -> dict[str, str]:
        return {
            "ToUserName": self.corp_id,
            "FromUserName": userid,
            "CreateTime": str(int(time.time())),
            "MsgType": "text",
            "Content": content,
            "MsgId": msg_id,
            "AgentID": self.agent_id,
        }

    def card_click(self, userid: str, task_id: str, key: str) -> dict[str, str]:
        """审批卡片按钮：key 是 approve（通过）或 return（退回）。"""
        return {
            "ToUserName": self.corp_id,
            "FromUserName": userid,
            "CreateTime": str(int(time.time())),
            "MsgType": "event",
            "Event": "template_card_event",
            "EventKey": key,
            "TaskId": task_id,
            "CardType": "button_interaction",
            "ResponseCode": "e2e-" + secrets.token_hex(16),
            "AgentID": self.agent_id,
        }

    def post(self, fields: dict[str, str], *, forge: bool = False) -> httpx.Response:
        """加密推送；forge=True 时把签名改掉一位，模拟伪造的请求。"""
        encrypted = self._encrypt(_xml(fields))
        stamp, nonce = str(int(time.time())), secrets.token_hex(8)
        sig = _signature(self._token, stamp, nonce, encrypted)
        if forge:
            sig = ("0" if sig[0] != "0" else "1") + sig[1:]
        body = _xml({"ToUserName": self.corp_id, "Encrypt": encrypted, "AgentID": self.agent_id})
        # 企业微信服务器不带 Cookie；回调入口见到 Cookie 会直接拒绝，所以每次清掉 Frappe 发的
        self.http.cookies.clear()
        return self.http.post(
            self.url,
            params={"msg_signature": sig, "timestamp": stamp, "nonce": nonce},
            content=body,
            headers={"Content-Type": "application/xml"},
        )

    def _encrypt(self, message: bytes) -> str:
        raw = secrets.token_bytes(16) + struct.pack("!I", len(message)) + message
        raw += self.corp_id.encode()
        pad = 32 - len(raw) % 32
        enc = Cipher(algorithms.AES(self._key), modes.CBC(self._key[:16])).encryptor()
        return base64.b64encode(enc.update(raw + bytes([pad]) * pad) + enc.finalize()).decode()


def _signature(token: str, stamp: str, nonce: str, encrypted: str) -> str:
    joined = "".join(sorted((token, stamp, nonce, encrypted)))
    return hashlib.sha1(joined.encode()).hexdigest()  # noqa: S324 企业微信协议规定 SHA1


def _xml(fields: dict[str, str]) -> bytes:
    root = ET.Element("xml")
    for name, value in fields.items():
        ET.SubElement(root, name).text = value
    data: bytes = ET.tostring(root, encoding="utf-8")
    return data
