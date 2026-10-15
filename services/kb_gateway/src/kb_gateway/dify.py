"""调用 Dify 知识库检索 API。

接口：`POST {api_base}/datasets/{dataset_id}/retrieve`。
请求和返回字段以 P0 验证清单 3.3 的实测为准，
解析集中在本文件，字段变化只改这里。
"""

from typing import Any, Protocol

import httpx

from kb_gateway.config import Settings
from kb_gateway.models import RawRecord


class Knowledge(Protocol):
    async def retrieve(
        self, dataset_id: str, query: str, top_k: int, conditions: dict[str, object]
    ) -> list[RawRecord]: ...


class DifyKnowledge:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._client = client
        self._base = str(settings.dify_api_base).rstrip("/")
        key = settings.dify_dataset_api_key.get_secret_value()
        self._headers = {"Authorization": f"Bearer {key}"}

    async def retrieve(
        self, dataset_id: str, query: str, top_k: int, conditions: dict[str, object]
    ) -> list[RawRecord]:
        body = {
            "query": query,
            "retrieval_model": {
                "search_method": "hybrid_search",
                "reranking_enable": False,
                "top_k": top_k,
                "score_threshold_enabled": False,
                "metadata_filtering_conditions": conditions,
            },
        }
        resp = await self._client.post(
            f"{self._base}/datasets/{dataset_id}/retrieve", headers=self._headers, json=body
        )
        resp.raise_for_status()
        return [_parse(r) for r in resp.json().get("records", [])]


def _parse(record: dict[str, Any]) -> RawRecord:
    segment = record.get("segment") or {}
    document = segment.get("document") or {}
    metadata = document.get("doc_metadata") or {}
    return RawRecord(
        title=str(document.get("name", "")),
        content=str(segment.get("content", "")),
        score=float(record.get("score") or 0.0),
        document_id=str(document.get("id", segment.get("document_id", ""))),
        metadata=metadata if isinstance(metadata, dict) else {},
    )
