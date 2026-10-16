"""调用 Dify 知识库 API 管理文档和元数据。

接口和返回字段以本地实测（Dify 1.17.1）为准，解析集中在本文件：
- 文档列表 `GET /datasets/{id}/documents` 的 `doc_metadata` 是 `[{id, name, type, value}]`
- 文档还在索引时 `update-by-text` 会返回 400，所以修改时新建一份再删掉旧的
"""

from typing import Any, Protocol

import httpx

from policy_sync.config import Settings
from policy_sync.models import DifyDoc


class Knowledge(Protocol):
    async def list_docs(self, dataset_id: str) -> list[DifyDoc]: ...

    async def create_doc(self, dataset_id: str, name: str, text: str) -> str: ...

    async def set_metadata(self, dataset_id: str, doc_id: str, values: dict[str, str]) -> None: ...

    async def delete_doc(self, dataset_id: str, doc_id: str) -> None: ...


class DifyKnowledge:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._s = settings
        self._client = client
        self._base = str(settings.dify_api_base).rstrip("/")
        key = settings.dify_dataset_api_key.get_secret_value()
        self._headers = {"Authorization": f"Bearer {key}"}
        # 每个知识库的元数据字段名到字段 id
        self._fields: dict[str, dict[str, str]] = {}

    async def list_docs(self, dataset_id: str) -> list[DifyDoc]:
        out: list[DifyDoc] = []
        page = 1
        while True:
            body = await self._req(
                "GET", f"/datasets/{dataset_id}/documents", params={"page": page, "limit": 100}
            )
            out += [_parse_doc(d) for d in body.get("data", [])]
            if not body.get("has_more"):
                return out
            page += 1

    async def create_doc(self, dataset_id: str, name: str, text: str) -> str:
        body = await self._req(
            "POST",
            f"/datasets/{dataset_id}/document/create-by-text",
            json={
                "name": name,
                "text": text,
                "indexing_technique": self._s.dify_indexing_technique,
                "process_rule": {"mode": "automatic"},
            },
        )
        return str(body["document"]["id"])

    async def set_metadata(self, dataset_id: str, doc_id: str, values: dict[str, str]) -> None:
        fields = await self._ensure_fields(dataset_id, list(values))
        items = [{"id": fields[k], "name": k, "value": v} for k, v in values.items()]
        await self._req(
            "POST",
            f"/datasets/{dataset_id}/documents/metadata",
            json={"operation_data": [{"document_id": doc_id, "metadata_list": items}]},
        )

    async def delete_doc(self, dataset_id: str, doc_id: str) -> None:
        resp = await self._client.delete(
            f"{self._base}/datasets/{dataset_id}/documents/{doc_id}", headers=self._headers
        )
        if resp.status_code != 404:  # 已经不在了也算删除成功
            resp.raise_for_status()

    async def _ensure_fields(self, dataset_id: str, names: list[str]) -> dict[str, str]:
        known = self._fields.get(dataset_id)
        if known is None or any(n not in known for n in names):
            body = await self._req("GET", f"/datasets/{dataset_id}/metadata")
            known = {str(f["name"]): str(f["id"]) for f in body.get("doc_metadata", [])}
            for n in names:
                if n not in known:
                    f = await self._req(
                        "POST",
                        f"/datasets/{dataset_id}/metadata",
                        json={"type": "string", "name": n},
                    )
                    known[n] = str(f["id"])
            self._fields[dataset_id] = known
        return known

    async def _req(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        resp = await self._client.request(method, self._base + path, headers=self._headers, **kw)
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        return body


def _parse_doc(d: dict[str, Any]) -> DifyDoc:
    meta = d.get("doc_metadata") or []
    values = {
        str(m["name"]): str(m["value"])
        for m in meta
        if isinstance(m, dict) and m.get("id") != "built-in" and m.get("value") is not None
    }
    return DifyDoc(id=str(d["id"]), metadata=values)
