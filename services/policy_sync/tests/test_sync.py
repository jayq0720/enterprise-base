import httpx

from policy_sync.config import Settings
from policy_sync.sync import Outcome, Syncer, with_retry
from tests.conftest import FakeKnowledge, FakeWiki


def make(settings: Settings) -> tuple[Syncer, FakeWiki, FakeKnowledge]:
    wiki, kb = FakeWiki(), FakeKnowledge()
    return Syncer(settings, wiki, kb), wiki, kb


async def test_publish_creates_doc_with_scope(settings: Settings) -> None:
    syncer, wiki, kb = make(settings)
    wiki.put("WD1", "入职满一年五天年假", space="sp-hr")
    assert await syncer.sync_page("WD1") == Outcome.CREATED
    [(ds, doc)] = kb.copies("WD1")
    assert ds == "ds-hr"
    assert doc.metadata["eb_scope"] == ",dept:人事部 - TC,desig:HRBP,"
    assert kb.texts[doc.id].startswith("# WD1 标题")


async def test_repeat_push_is_idempotent(settings: Settings) -> None:
    syncer, wiki, kb = make(settings)
    wiki.put("WD1", "正文")
    await syncer.sync_page("WD1")
    assert await syncer.sync_page("WD1") == Outcome.UNCHANGED
    assert kb.created == 1 and len(kb.copies("WD1")) == 1


async def test_edit_replaces_old_copy(settings: Settings) -> None:
    syncer, wiki, kb = make(settings)
    wiki.put("WD1", "旧正文")
    await syncer.sync_page("WD1")
    wiki.put("WD1", "新正文")
    assert await syncer.sync_page("WD1") == Outcome.CREATED
    [(_, doc)] = kb.copies("WD1")
    assert "新正文" in kb.texts[doc.id]


async def test_scope_change_updates_metadata_only(settings: Settings) -> None:
    syncer, wiki, kb = make(settings)
    wiki.put("WD1", "正文")
    await syncer.sync_page("WD1")
    wiki.binds[0].scope = ["desig:总经理"]
    assert await syncer.sync_page("WD1") == Outcome.RESCOPED
    [(_, doc)] = kb.copies("WD1")
    assert kb.created == 1 and doc.metadata["eb_scope"] == ",desig:总经理,"


async def test_unpublish_and_delete_remove_copies(settings: Settings) -> None:
    syncer, wiki, kb = make(settings)
    wiki.put("WD1", "正文")
    wiki.put("WD2", "正文")
    await syncer.sync_page("WD1")
    await syncer.sync_page("WD2")
    wiki.put("WD1", "正文", published=False)
    del wiki.pages["WD2"]
    assert await syncer.sync_page("WD1") == Outcome.REMOVED
    assert await syncer.sync_page("WD2") == Outcome.REMOVED
    assert kb.copies("WD1") == [] and kb.copies("WD2") == []


async def test_move_to_other_space_moves_dataset(settings: Settings) -> None:
    syncer, wiki, kb = make(settings)
    wiki.put("WD1", "正文", space="sp-policy")
    await syncer.sync_page("WD1")
    wiki.put("WD1", "正文", space="sp-hr")
    await syncer.sync_page("WD1")
    assert [ds for ds, _ in kb.copies("WD1")] == ["ds-hr"]


async def test_unbound_space_and_bad_scope_fail_closed(settings: Settings) -> None:
    syncer, wiki, kb = make(settings)
    wiki.put("WD1", "正文", space="sp-policy")
    await syncer.sync_page("WD1")
    wiki.binds[0].scope = ["dept:销售部,人事部"]  # 名称里带逗号
    assert await syncer.sync_page("WD1") == Outcome.REMOVED
    wiki.put("WD2", "正文", space="没绑定的空间")
    assert await syncer.sync_page("WD2") == Outcome.REMOVED
    assert kb.copies("WD1") == [] and kb.copies("WD2") == []


async def test_reconcile_adds_missing_and_drops_orphans(settings: Settings) -> None:
    syncer, wiki, kb = make(settings)
    wiki.put("WD1", "正文")
    await syncer.sync_page("WD1")
    del wiki.pages["WD1"]  # 删除推送丢了
    wiki.put("WD2", "正文", space="sp-hr")  # 发布推送丢了
    counts = await syncer.reconcile()
    assert counts == {"removed": 1, "created": 1}
    assert kb.copies("WD1") == [] and len(kb.copies("WD2")) == 1


async def test_with_retry_recovers_from_transient_error(settings: Settings) -> None:
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("down")
        return "ok"

    assert await with_retry(settings, "测试", flaky) == "ok" and calls == 3


async def test_with_retry_gives_up(settings: Settings) -> None:
    async def broken() -> str:
        raise httpx.ConnectError("down")

    assert await with_retry(settings, "测试", broken) is None
