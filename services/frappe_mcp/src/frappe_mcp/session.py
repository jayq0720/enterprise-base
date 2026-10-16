"""签名令牌：把“谁在问”“要提交什么”绑定在服务端签发的短期令牌里，见 ADR 0001。

令牌 = base64url(载荷).base64url(HMAC-SHA256(载荷))，载荷带用途 `k` 和过期时间 `exp`。
- 会话令牌（k=session）：{"uid": 企业微信 userid}，工具只认令牌里的 uid，
  模型就算被诱导填别人的名字，也伪造不出别人的令牌。
- 请假草稿（k=leave）：草稿内容和 uid，员工确认后原样提交，模型改不了内容。
两种令牌用途不同，不能互相顶替。
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

SESSION = "session"
LEAVE_DRAFT = "leave"


class SessionError(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: bytes, payload: str) -> str:
    return _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())


def sign(secret: bytes, kind: str, data: dict[str, Any], ttl: int, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl)
    body = {**data, "k": kind, "exp": exp}
    payload = _b64(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{payload}.{_mac(secret, payload)}"


def unsign(secret: bytes, kind: str, token: str, now: float | None = None) -> dict[str, Any]:
    """验签并返回载荷；签名不对、用途不对或过期就抛 SessionError。"""
    payload, _, sig = token.strip().partition(".")
    if not payload or not hmac.compare_digest(sig, _mac(secret, payload)):
        raise SessionError("会话令牌无效")
    try:
        data = json.loads(_unb64(payload))
        exp = int(data["exp"])
        ok = isinstance(data, dict) and data.get("k") == kind and isinstance(data.get("uid"), str)
    except (ValueError, KeyError, TypeError) as e:
        raise SessionError("会话令牌无效") from e
    if not ok:
        raise SessionError("会话令牌无效")
    if exp < (now if now is not None else time.time()):
        raise SessionError("会话已过期，请重新提问")
    result: dict[str, Any] = data
    return result


def issue(secret: bytes, user_id: str, ttl: int, now: float | None = None) -> str:
    return sign(secret, SESSION, {"uid": user_id}, ttl, now)


def verify(secret: bytes, token: str, now: float | None = None) -> str:
    """返回会话令牌里的企业微信 userid。"""
    return str(unsign(secret, SESSION, token, now)["uid"])
