"""签名 Cookie：登录会话和扫码登录的一次性 state。

令牌 = base64url(载荷).base64url(HMAC-SHA256(载荷))，载荷带用途 `k` 和过期时间 `exp`，
做法和 X4 的会话令牌一样（ADR 0001）。浏览器只拿到签名后的令牌，改不了里面的 userid。
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

LOGIN = "portal-session"
STATE = "portal-state"


class SessionError(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: bytes, payload: str) -> str:
    return _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sign(secret: bytes, kind: str, data: dict[str, Any], ttl: int, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl)
    body = {**data, "k": kind, "exp": exp}
    payload = _b64(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{payload}.{_mac(secret, payload)}"


def unsign(secret: bytes, kind: str, token: str, now: float | None = None) -> dict[str, Any]:
    """验签并返回载荷；签名不对、用途不对或过期就抛 SessionError。"""
    payload, _, sig = token.strip().partition(".")
    if not payload or len(token) > 4096 or not hmac.compare_digest(sig, _mac(secret, payload)):
        raise SessionError("invalid")
    try:
        data = json.loads(_unb64(payload))
        exp = int(data["exp"])
        ok = isinstance(data, dict) and data.get("k") == kind
    except (ValueError, KeyError, TypeError) as e:
        raise SessionError("invalid") from e
    if not ok:
        raise SessionError("invalid")
    if exp < (now if now is not None else time.time()):
        raise SessionError("expired")
    result: dict[str, Any] = data
    return result
