import httpx
import pytest

from kb_gateway.config import Settings
from kb_gateway.models import EmployeeProfile, SearchRequest
from kb_gateway.service import NO_PROFILE, NO_SPACE, UNAVAILABLE, SearchService

from .conftest import HRBP, POLICY, SALES, FakeDirectory, FakeKnowledge, rec

DATA = {
    "ds-policy": [rec("考勤制度", 0.9, ",all,"), rec("人事内部规程", 0.8, ",dept:人事部,")],
    "ds-hr": [rec("HRBP 手册", 0.95, ",desig:HRBP,"), rec("缺范围标记", 0.99, None)],
}


def make(settings: Settings) -> tuple[SearchService, FakeKnowledge]:
    k = FakeKnowledge(DATA)
    d = FakeDirectory({"hrbp": HRBP, "sales": SALES})
    return SearchService(settings, d, k), k


async def test_unknown_user_denied(settings: Settings) -> None:
    svc, k = make(settings)
    resp = await svc.search(SearchRequest(user_id="nobody", query="请假"))
    assert resp.records == [] and resp.notice == NO_PROFILE
    assert k.calls == []


async def test_no_designation_sees_only_public(settings: Settings) -> None:
    # 在职但还没配岗位：只看全员可见内容，即使部门匹配也不看部门文档
    svc, k = make(settings)
    profile = EmployeeProfile(employee="E", department="人事部", designation=None, spaces=[POLICY])
    svc._directory = FakeDirectory({"x": profile})
    resp = await svc.search(SearchRequest(user_id="x", query="请假"))
    assert [c.title for c in resp.records] == ["考勤制度"] and resp.notice is None
    assert k.calls[0][1]["conditions"] == [
        {"name": "eb_scope", "comparison_operator": "contains", "value": ",all,"}
    ]


async def test_directory_error_returns_notice(settings: Settings) -> None:
    class Down:
        async def get_profile(self, user_id: str) -> EmployeeProfile | None:
            raise httpx.ConnectError("down")

    svc, k = make(settings)
    svc._directory = Down()
    resp = await svc.search(SearchRequest(user_id="hrbp", query="请假"))
    assert resp.notice == UNAVAILABLE and k.calls == []


async def test_dify_error_partial_and_total(settings: Settings) -> None:
    svc, k = make(settings)
    k.broken = {"ds-hr"}
    resp = await svc.search(SearchRequest(user_id="hrbp", query="请假"))
    assert [c.title for c in resp.records] == ["考勤制度", "人事内部规程"]
    k.broken = {"ds-hr", "ds-policy"}
    resp = await svc.search(SearchRequest(user_id="hrbp", query="请假"))
    assert resp.records == [] and resp.notice == UNAVAILABLE


async def test_no_space(settings: Settings) -> None:
    svc, _ = make(settings)
    svc._directory = FakeDirectory({"x": SALES.model_copy(update={"spaces": []})})
    resp = await svc.search(SearchRequest(user_id="x", query="请假"))
    assert resp.notice == NO_SPACE


async def test_sales_only_sees_public_in_own_spaces(settings: Settings) -> None:
    svc, k = make(settings)
    resp = await svc.search(SearchRequest(user_id="sales", query="请假"))
    assert [c.title for c in resp.records] == ["考勤制度"]
    assert [c[0] for c in k.calls] == ["ds-policy"]


async def test_hrbp_sees_scoped_docs_sorted_and_capped(settings: Settings) -> None:
    svc, _ = make(settings)
    resp = await svc.search(SearchRequest(user_id="hrbp", query="请假", top_k=50))
    # 缺范围标记的文档被丢弃；top_k 被限制为 top_k_max=3
    assert [c.title for c in resp.records] == ["HRBP 手册", "考勤制度", "人事内部规程"]
    assert resp.records[0].space == "人事手册"


async def test_dropped_records_do_not_log_query(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    svc, _ = make(settings)
    await svc.search(SearchRequest(user_id="hrbp", query="我的工资是多少"))
    assert "工资" not in caplog.text
