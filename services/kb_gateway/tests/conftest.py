import pytest

from kb_gateway.config import Settings
from kb_gateway.models import EmployeeProfile, KnowledgeSpace, RawRecord

TOKEN = "t" * 40


@pytest.fixture
def settings() -> Settings:
    return Settings(
        kb_service_token=TOKEN,
        frappe_url="http://frappe.test",
        frappe_api_key="k",
        frappe_api_secret="s",
        dify_api_base="http://dify.test/v1",
        dify_dataset_api_key="d",
        top_k_max=3,
    )


class FakeDirectory:
    def __init__(self, profiles: dict[str, EmployeeProfile]) -> None:
        self.profiles = profiles

    async def get_profile(self, user_id: str) -> EmployeeProfile | None:
        return self.profiles.get(user_id)


class FakeKnowledge:
    def __init__(self, data: dict[str, list[RawRecord]]) -> None:
        self.data = data
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def retrieve(
        self, dataset_id: str, query: str, top_k: int, conditions: dict[str, object]
    ) -> list[RawRecord]:
        self.calls.append((dataset_id, conditions))
        return self.data.get(dataset_id, [])


def rec(title: str, score: float, scope: str | None) -> RawRecord:
    meta: dict[str, object] = {} if scope is None else {"eb_scope": scope}
    return RawRecord(
        title=title, content=f"{title} 正文", score=score, document_id=title, metadata=meta
    )


HRBP = EmployeeProfile(
    employee="HR-EMP-0001",
    department="人事部",
    designation="HRBP",
    spaces=[
        KnowledgeSpace(name="公司制度", dataset_id="ds-policy"),
        KnowledgeSpace(name="人事手册", dataset_id="ds-hr"),
    ],
)
SALES = EmployeeProfile(
    employee="HR-EMP-0002",
    department="销售部",
    designation="销售代表",
    spaces=[KnowledgeSpace(name="公司制度", dataset_id="ds-policy")],
)
