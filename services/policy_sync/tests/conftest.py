import pytest

from policy_sync.config import Settings
from policy_sync.models import DifyDoc, SpaceBinding, WikiPage

SECRET = "w" * 40
TOKEN = "t" * 40
HR_SCOPE = ["dept:人事部 - TC", "desig:HRBP"]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        sync_webhook_secret=SECRET,
        sync_service_token=TOKEN,
        frappe_url="http://frappe.test",
        frappe_api_key="k",
        frappe_api_secret="s",
        dify_api_base="http://dify.test/v1",
        dify_dataset_api_key="d",
        retry_attempts=3,
        retry_base_seconds=0,
        reconcile_interval_seconds=0,
    )


class FakeWiki:
    def __init__(self) -> None:
        self.pages: dict[str, WikiPage] = {}
        self.binds = [
            SpaceBinding(wiki_space="sp-policy", dify_dataset_id="ds-policy", scope=["all"]),
            SpaceBinding(wiki_space="sp-hr", dify_dataset_id="ds-hr", scope=HR_SCOPE),
        ]

    def put(
        self, name: str, content: str, space: str = "sp-policy", published: bool = True
    ) -> None:
        self.pages[name] = WikiPage(
            name=name, title=f"{name} 标题", content=content, published=published, wiki_space=space
        )

    async def bindings(self) -> list[SpaceBinding]:
        return list(self.binds)

    async def get_page(self, name: str) -> WikiPage | None:
        return self.pages.get(name)

    async def published_pages(self, wiki_space: str) -> list[str]:
        return [p.name for p in self.pages.values() if p.wiki_space == wiki_space and p.published]


class FakeKnowledge:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, DifyDoc]] = {"ds-policy": {}, "ds-hr": {}}
        self.texts: dict[str, str] = {}
        self.created = 0

    async def list_docs(self, dataset_id: str) -> list[DifyDoc]:
        return [d.model_copy(deep=True) for d in self.docs[dataset_id].values()]

    async def create_doc(self, dataset_id: str, name: str, text: str) -> str:
        self.created += 1
        doc_id = f"doc{self.created}"
        self.docs[dataset_id][doc_id] = DifyDoc(id=doc_id, metadata={})
        self.texts[doc_id] = text
        return doc_id

    async def set_metadata(self, dataset_id: str, doc_id: str, values: dict[str, str]) -> None:
        self.docs[dataset_id][doc_id].metadata.update(values)

    async def delete_doc(self, dataset_id: str, doc_id: str) -> None:
        self.docs[dataset_id].pop(doc_id, None)

    def copies(self, source: str) -> list[tuple[str, DifyDoc]]:
        return [
            (ds, d)
            for ds, items in self.docs.items()
            for d in items.values()
            if d.metadata.get("eb_source") == source
        ]
