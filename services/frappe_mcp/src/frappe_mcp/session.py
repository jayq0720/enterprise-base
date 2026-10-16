"""会话令牌：把“谁在问”绑定在服务端签发的短期令牌里，见 ADR 0001。

令牌 = base64url(载荷).base64url(HMAC-SHA256(载荷))，
载荷是 {"uid": 企业微信 userid, "exp": 过期时间}。
工具只认令牌里的 uid，模型就算被诱导填别人的名字，也伪造不出别人的令牌。
"""

import base64
import hashlib
import hmac
import json
import time


class SessionError(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(secret: bytes, user_id: str, ttl: int, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl)
    payload = _b64(json.dumps({"uid": user_id, "exp": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify(secret: bytes, token: str, now: float | None = None) -> str:
    """返回令牌里的企业微信 userid；签名不对或过期就抛 SessionError。"""
    payload, _, sig = token.strip().partition(".")
    expected = _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    if not payload or not hmac.compare_digest(sig, expected):
        raise SessionError("会话令牌无效")
    try:
        data = json.loads(_unb64(payload))
        uid, exp = str(data["uid"]), int(data["exp"])
    except (ValueError, KeyError, TypeError) as e:
        raise SessionError("会话令牌无效") from e
    if exp < (now if now is not None else time.time()):
        raise SessionError("会话已过期，请重新提问")
    return uid
