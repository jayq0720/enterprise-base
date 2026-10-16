import json

import httpx

from policy_sync.config import Settings
from policy_sync.dify import DifyKnowledge
from policy_sync.frappe import FrappeWiki


async def test_frappe_page_space_found_through_ancestors(settings: Settings) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/resource/Wiki Document/WD1":
            d = {"name": "WD1", "title": "年假", "content": "正文", "is_published": 1}
            return httpx.Response(200, json={"data": {**d, "lft": 2, "rgt": 3, "is_group": 0}})
        filters = json.loads(req.url.params["filters"])
        if path == "/api/resource/Wiki Document":
            assert ["lft", "<", 2] in filters and ["rgt", ">", 3] in filters
            return httpx.Response(200, json={"data": [{"name": "ROOT"}]})
        assert path == "/api/resource/Wiki Space"
        assert filters == [["root_group", "in", ["ROOT"]]]
        return httpx.Response(200, json={"data": [{"name": "sp-policy"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        page = await FrappeWiki(settings, c).get_page("WD1")
    assert page is not None and page.wiki_space == "sp-policy" and page.published


async def test_frappe_deleted_page_and_group_return_none(settings: Settings) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/GONE"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"data": {"name": "G", "is_group": 1, "lft": 1, "rgt": 9}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        w = FrappeWiki(settings, c)
        assert await w.get_page("GONE") is None
        assert await w.get_page("G") is None


async def test_frappe_bindings_skip_incomplete(settings: Settings) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/method/enterprise_base.duties.api.knowledge_spaces"
        items = [
            {"space": "制度", "wiki_space": "sp1", "dify_dataset_id": "ds1", "scope": ["all"]},
            {"space": "未绑定", "wiki_space": "sp2", "dify_dataset_id": "", "scope": ["all"]},
        ]
        return httpx.Response(200, json={"message": items})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await FrappeWiki(settings, c).bindings()
    assert [b.dify_dataset_id for b in out] == ["ds1"]


async def test_dify_metadata_fields_created_once(settings: Settings) -> None:
    created: list[str] = []
    ops: list[object] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/v1/datasets/ds1/metadata":
            return httpx.Response(200, json={"doc_metadata": [{"id": "f1", "name": "eb_scope"}]})
        if req.url.path == "/v1/datasets/ds1/metadata":
            name = json.loads(req.content)["name"]
            created.append(name)
            return httpx.Response(200, json={"id": f"id-{name}", "name": name})
        ops.append(json.loads(req.content)["operation_data"])
        return httpx.Response(200, json={"result": "success"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        kb = DifyKnowledge(settings, c)
        await kb.set_metadata("ds1", "doc1", {"eb_scope": ",all,", "eb_source": "WD1"})
        await kb.set_metadata("ds1", "doc2", {"eb_scope": ",all,", "eb_source": "WD2"})
    assert created == ["eb_source"]
    first = ops[0]
    assert first == [
        {
            "document_id": "doc1",
            "metadata_list": [
                {"id": "f1", "name": "eb_scope", "value": ",all,"},
                {"id": "id-eb_source", "name": "eb_source", "value": "WD1"},
            ],
        }
    ]


async def test_dify_list_docs_pages_and_skips_builtin(settings: Settings) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        page = int(req.url.params["page"])
        meta = [
            {"id": "f1", "name": "eb_source", "value": f"WD{page}"},
            {"id": "built-in", "name": "document_name", "value": "x"},
        ]
        return httpx.Response(
            200, json={"data": [{"id": f"doc{page}", "doc_metadata": meta}], "has_more": page < 2}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        docs = await DifyKnowledge(settings, c).list_docs("ds1")
    assert [(d.id, d.metadata) for d in docs] == [
        ("doc1", {"eb_source": "WD1"}),
        ("doc2", {"eb_source": "WD2"}),
    ]


async def test_dify_delete_missing_doc_is_ok(settings: Settings) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "DELETE"
        return httpx.Response(404, json={"code": "not_found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await DifyKnowledge(settings, c).delete_doc("ds1", "doc1")
