"""同步逻辑：让 Dify 里每个 Wiki 页面恰好有一份、在正确的知识库、带正确的可见范围。

不在本地存状态。Dify 文档的元数据 `eb_source` 记着它来自哪个 Wiki 页面，
每次同步都按它在所有绑定的知识库里找已有副本，所以页面换了空间、
重复推送、服务重启都不会留下多余的副本。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum

import httpx

from policy_sync.config import Settings
from policy_sync.dify import Knowledge
from policy_sync.frappe import Wiki
from policy_sync.models import DifyDoc, SpaceBinding, WikiPage
from policy_sync.scope import ScopeError, build_scope, content_hash, render_text

log = logging.getLogger("policy_sync")


class Outcome(StrEnum):
    CREATED = "created"  # 新建或正文变化后重建
    RESCOPED = "rescoped"  # 正文没变，只更新可见范围
    UNCHANGED = "unchanged"
    REMOVED = "removed"  # 下架、删除、移出绑定空间，或可见范围无效


class Syncer:
    def __init__(self, settings: Settings, wiki: Wiki, knowledge: Knowledge) -> None:
        self._s = settings
        self._wiki = wiki
        self._kb = knowledge
        self._locks: dict[str, asyncio.Lock] = {}

    async def sync_page(self, name: str) -> Outcome:
        async with self._locks.setdefault(name, asyncio.Lock()):
            bindings = await self._wiki.bindings()
            docs = {
                b.dify_dataset_id: await self._kb.list_docs(b.dify_dataset_id) for b in bindings
            }
            page = await self._wiki.get_page(name)
            return await self._apply(name, page, bindings, docs)

    async def reconcile(self) -> dict[str, int]:
        """全量对账：已发布的页面补齐，Dify 里来源已不存在或已下架的副本删掉。"""
        bindings = await self._wiki.bindings()
        docs = {b.dify_dataset_id: await self._kb.list_docs(b.dify_dataset_id) for b in bindings}
        names: set[str] = set()
        for b in bindings:
            names.update(await self._wiki.published_pages(b.wiki_space))
        src = self._s.source_metadata_field
        names.update(d.metadata[src] for ds in docs.values() for d in ds if d.metadata.get(src))
        counts: dict[str, int] = {}
        for name in sorted(names):
            try:
                async with self._locks.setdefault(name, asyncio.Lock()):
                    page = await self._wiki.get_page(name)
                    outcome: str = await self._apply(name, page, bindings, docs)
            except httpx.HTTPError as e:
                log.error("页面 %s 对账失败：%s", name, type(e).__name__)
                outcome = "failed"
            counts[outcome] = counts.get(outcome, 0) + 1
        log.info("对账完成 %s", counts)
        return counts

    async def _apply(
        self,
        name: str,
        page: WikiPage | None,
        bindings: list[SpaceBinding],
        docs: dict[str, list[DifyDoc]],
    ) -> Outcome:
        src, scope_f, hash_f = (
            self._s.source_metadata_field,
            self._s.scope_metadata_field,
            self._s.hash_metadata_field,
        )
        target, scope = self._target(page, bindings)
        outcome = Outcome.REMOVED
        keep: str | None = None
        if page and target and scope:
            mine = [d for d in docs[target.dify_dataset_id] if d.metadata.get(src) == name]
            h = content_hash(page.title, page.content)
            same = next((d for d in mine if d.metadata.get(hash_f) == h), None)
            values = {scope_f: scope, src: name, hash_f: h}
            if same is None:
                # 先建新的再删旧的，中间不出现查不到的空档；新文档写入范围前任何人都检索不到
                keep = await self._kb.create_doc(
                    target.dify_dataset_id, page.title, render_text(page.title, page.content)
                )
                await self._kb.set_metadata(target.dify_dataset_id, keep, values)
                outcome = Outcome.CREATED
            else:
                keep = same.id
                outcome = Outcome.UNCHANGED
                if same.metadata.get(scope_f) != scope:
                    await self._kb.set_metadata(target.dify_dataset_id, keep, values)
                    outcome = Outcome.RESCOPED
        for ds, items in docs.items():
            for d in items:
                if d.metadata.get(src) == name and d.id != keep:
                    await self._kb.delete_doc(ds, d.id)
        log.info("页面 %s 同步结果 %s", name, outcome)
        return outcome

    def _target(
        self, page: WikiPage | None, bindings: list[SpaceBinding]
    ) -> tuple[SpaceBinding | None, str | None]:
        if page is None or not page.published or page.wiki_space is None:
            return None, None
        target = next((b for b in bindings if b.wiki_space == page.wiki_space), None)
        if target is None:
            return None, None
        try:
            return target, build_scope(target.scope)
        except ScopeError as e:
            # 范围无效时按不可见处理（删除副本），不冒险用错误的范围发布
            log.error("页面 %s 所在空间 %s 的可见范围无效：%s", page.name, page.wiki_space, e)
            return None, None


async def with_retry(
    settings: Settings, label: str, fn: Callable[[], Awaitable[object]]
) -> object | None:
    """失败按指数退避重试；最终失败只记日志，等定时对账补上。"""
    for attempt in range(1, settings.retry_attempts + 1):
        try:
            return await fn()
        except (httpx.HTTPError, KeyError, ValueError) as e:
            status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
            if attempt == settings.retry_attempts:
                log.error("%s 失败，已重试 %d 次：%s %s", label, attempt, type(e).__name__, status)
                return None
            log.warning("%s 第 %d 次失败：%s %s", label, attempt, type(e).__name__, status)
            await asyncio.sleep(settings.retry_base_seconds * 2 ** (attempt - 1))
    return None
