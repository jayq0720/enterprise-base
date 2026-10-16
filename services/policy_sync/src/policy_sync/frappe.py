"""到 Frappe 读 Wiki 页面和 X1 的知识空间绑定（只走 REST，不 import Frappe 代码）。

Wiki 3.4 的页面是 `Wiki Document`，按嵌套集合（lft/rgt）组成树；只有空间的根节点
在 `Wiki Space.root_group` 上，子页面的 `wiki_space` 字段是空的，所以要按祖先找空间。
"""

import json
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from policy_sync.config import Settings
from policy_sync.models import SpaceBinding, WikiPage

DOC = "Wiki Document"
SPACE = "Wiki Space"


class Wiki(Protocol):
    async def bindings(self) -> list[SpaceBinding]: ...

    async def get_page(self, name: str) -> WikiPage | None:
        """页面已删除时返回 None；分组节点（目录）也返回 None。"""
        ...

    async def published_pages(self, wiki_space: str) -> list[str]: ...


class FrappeWiki:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._s = settings
        self._client = client
        key = settings.frappe_api_key.get_secret_value()
        secret = settings.frappe_api_secret.get_secret_value()
        self._headers = {"Authorization": f"token {key}:{secret}"}
        self._base = str(settings.frappe_url).rstrip("/")

    async def bindings(self) -> list[SpaceBinding]:
        resp = await self._client.get(
            f"{self._base}/api/method/{self._s.frappe_bindings_method}", headers=self._headers
        )
        resp.raise_for_status()
        return [
            SpaceBinding.model_validate(i)
            for i in resp.json().get("message", [])
            if i.get("wiki_space") and i.get("dify_dataset_id")
        ]

    async def get_page(self, name: str) -> WikiPage | None:
        resp = await self._client.get(
            f"{self._base}/api/resource/{quote(DOC)}/{quote(name, safe='')}",
            headers=self._headers,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        d = resp.json()["data"]
        if d.get("is_group"):
            return None
        return WikiPage(
            name=d["name"],
            title=str(d.get("title") or d["name"]),
            content=str(d.get("content") or ""),
            published=bool(d.get("is_published")),
            wiki_space=await self._space_of(int(d["lft"]), int(d["rgt"])),
        )

    async def published_pages(self, wiki_space: str) -> list[str]:
        space = await self._get(SPACE, [["name", "=", wiki_space]], ["root_group"])
        if not space or not space[0].get("root_group"):
            return []
        root = await self._get(DOC, [["name", "=", space[0]["root_group"]]], ["lft", "rgt"])
        if not root:
            return []
        rows = await self._get(
            DOC,
            [
                ["lft", ">", root[0]["lft"]],
                ["rgt", "<", root[0]["rgt"]],
                ["is_group", "=", 0],
                ["is_published", "=", 1],
            ],
            ["name"],
        )
        return [str(r["name"]) for r in rows]

    async def _space_of(self, lft: int, rgt: int) -> str | None:
        ancestors = await self._get(
            DOC, [["lft", "<", lft], ["rgt", ">", rgt], ["is_group", "=", 1]], ["name"]
        )
        if not ancestors:
            return None
        names = [str(a["name"]) for a in ancestors]
        spaces = await self._get(SPACE, [["root_group", "in", names]], ["name"])
        return str(spaces[0]["name"]) if len(spaces) == 1 else None

    async def _get(
        self, doctype: str, filters: list[list[object]], fields: list[str]
    ) -> list[dict[str, Any]]:
        resp = await self._client.get(
            f"{self._base}/api/resource/{quote(doctype)}",
            headers=self._headers,
            params={
                "filters": json.dumps(filters, ensure_ascii=False),
                "fields": json.dumps(fields),
                "limit_page_length": "0",
            },
        )
        resp.raise_for_status()
        data: list[dict[str, Any]] = resp.json().get("data", [])
        return data
