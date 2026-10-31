"""前台调用的后端：企业微信（扫码换 userid）、X4、X3、Dify 应用 API。

身份只在服务端传：userid 来自登录会话，不收浏览器传来的任何身份字段。
日志只记异常类型，不记地址（地址里有 userid、code）和返回内容。
"""

import logging
import time
from typing import Any, Protocol

import httpx

from portal.config import Settings

log = logging.getLogger("portal")


class Unavailable(Exception):
    """后端连不上或返回非 200。"""


class TooMany(Unavailable):
    """后端按员工限流，返回了 429。"""


class Api(Protocol):
    async def wecom_userid(self, code: str) -> str | None: ...

    async def x4(self, name: str, user_id: str, **args: str) -> dict[str, Any]: ...

    async def search(self, user_id: str, query: str) -> dict[str, Any]: ...

    async def intake(self, authorization: str, body: bytes) -> tuple[int, dict[str, Any]]: ...

    async def chat(
        self, key: str, user_id: str, query: str, conversation_id: str
    ) -> dict[str, Any]: ...


class Backends:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._s = settings
        self._client = client
        self._wecom_token: tuple[str, float] | None = None

    # —— 企业微信 ——
    async def wecom_userid(self, code: str) -> str | None:
        """扫码或企业微信内打开拿到的 code 换企业微信 userid；失败返回 None。"""
        token = await self._wecom_access_token()
        if not token:
            return None
        body = await self._wecom_get("auth/getuserinfo", {"access_token": token, "code": code})
        userid = body.get("userid") or body.get("UserId")
        return userid if isinstance(userid, str) and 0 < len(userid) <= 64 else None

    async def _wecom_access_token(self) -> str | None:
        if self._wecom_token and self._wecom_token[1] > time.monotonic():
            return self._wecom_token[0]
        body = await self._wecom_get(
            "gettoken",
            {
                "corpid": self._s.wecom_corp_id,
                "corpsecret": self._s.wecom_app_secret.get_secret_value(),
            },
        )
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            return None
        ttl = body.get("expires_in")
        ttl = ttl if isinstance(ttl, int) and ttl > 600 else 600
        self._wecom_token = (token, time.monotonic() + ttl - 300)
        return token

    async def _wecom_get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            resp = await self._client.get(
                str(self._s.wecom_api_base) + path, params=params, follow_redirects=False
            )
            body = resp.json() if resp.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as e:
            log.warning("wecom unavailable: %s", type(e).__name__)
            return {}
        if not isinstance(body, dict) or body.get("errcode", 0) != 0:
            log.info("wecom rejected: %s", body.get("errcode") if isinstance(body, dict) else "-")
            return {}
        return body

    # —— X4 ——
    async def x4(self, name: str, user_id: str, **args: str) -> dict[str, Any]:
        token = self._s.x4_portal_token.get_secret_value()
        return await self._post(
            str(self._s.x4_url).rstrip("/") + "/v1/portal/" + name,
            token,
            {**args, "user_id": user_id},
        )

    async def intake(self, authorization: str, body: bytes) -> tuple[int, dict[str, Any]]:
        """询盘进件原样转给 X4：Token 由网站后台带来，X4 校验，前台不保存。"""
        try:
            resp = await self._client.post(
                str(self._s.x4_url).rstrip("/") + "/v1/inquiry/intake",
                content=body,
                headers={"Authorization": authorization, "Content-Type": "application/json"},
                timeout=self._s.http_timeout_seconds,
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("backend unavailable: %s", type(e).__name__)
            raise Unavailable from e
        if not isinstance(data, dict) or resp.status_code not in (200, 401, 422, 429):
            raise Unavailable
        return resp.status_code, data

    # —— X3 ——
    async def search(self, user_id: str, query: str) -> dict[str, Any]:
        token = self._s.x3_token.get_secret_value()
        return await self._post(
            str(self._s.x3_url).rstrip("/") + "/v1/search",
            token,
            {"user_id": user_id, "query": query, "top_k": 5},
        )

    # —— Dify 应用 ——
    async def chat(
        self, key: str, user_id: str, query: str, conversation_id: str
    ) -> dict[str, Any]:
        body = {
            "inputs": {},
            "query": query,
            "response_mode": "blocking",
            "conversation_id": conversation_id,
            "user": user_id,
        }
        return await self._post(
            str(self._s.dify_api_base).rstrip("/") + "/chat-messages",
            key,
            body,
            timeout=self._s.chat_timeout_seconds,
        )

    async def _post(
        self, url: str, token: str, body: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        try:
            resp = await self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout or self._s.http_timeout_seconds,
            )
            if resp.status_code == 404 and "conversation" in resp.text.lower():
                # Dify 会话不存在（已过期或不是本人的）：让前台开新会话
                return {"_conversation_missing": True}
            if resp.status_code == 429:
                raise TooMany
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("backend unavailable: %s", type(e).__name__)
            raise Unavailable from e
        if not isinstance(data, dict):
            raise Unavailable
        return data
