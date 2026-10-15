import json

import httpx

from kb_gateway.config import Settings
from kb_gateway.dify import DifyKnowledge
from kb_gateway.directory import FrappeDirectory


async def test_frappe_directory_requests_only_safe_fields(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/api/resource/Employee":
            row = {"name": "E1", "department": "人事部", "designation": "HRBP"}
            return httpx.Response(200, json={"data": [row]})
        assert req.url.params["designation"] == "HRBP"
        spaces = [{"space": "人事手册", "dify_dataset_id": "ds-hr"}, {"space": "未绑定"}]
        return httpx.Response(200, json={"message": spaces})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        p = await FrappeDirectory(settings, c).get_profile("zhangsan")
    assert p is not None and [s.dataset_id for s in p.spaces] == ["ds-hr"]
    fields = json.loads(seen[0].url.params["fields"])
    assert fields == ["name", "department", "designation"]
    filters = json.loads(seen[0].url.params["filters"])
    assert ["wecom_userid", "=", "zhangsan"] in filters and ["status", "=", "Active"] in filters


async def test_frappe_directory_ambiguous_user_denied(settings: Settings) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"name": "E1"}, {"name": "E2"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await FrappeDirectory(settings, c).get_profile("dup") is None


async def test_dify_retrieve_parses_records(settings: Settings) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/datasets/ds-hr/retrieve"
        body = json.loads(req.content)
        assert body["retrieval_model"]["metadata_filtering_conditions"] == {"x": 1}
        doc = {"id": "doc1", "name": "考勤制度", "doc_metadata": {"eb_scope": ",all,"}}
        rec = {"segment": {"content": "第一条", "document": doc}, "score": 0.7}
        return httpx.Response(200, json={"records": [rec]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await DifyKnowledge(settings, c).retrieve("ds-hr", "q", 3, {"x": 1})
    assert out[0].title == "考勤制度" and out[0].metadata == {"eb_scope": ",all,"}
