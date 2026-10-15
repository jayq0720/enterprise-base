"""检索主流程：查身份 → 定可见范围 → 分空间检索 → 本地复核 → 合并排序。"""

import asyncio
import logging

from kb_gateway.config import Settings
from kb_gateway.dify import Knowledge
from kb_gateway.directory import Directory
from kb_gateway.models import Citation, SearchRequest, SearchResponse
from kb_gateway.scope import is_visible, metadata_conditions, viewer_tokens

log = logging.getLogger("kb_gateway")

NO_PROFILE = "未找到你的在职员工或岗位信息，请联系 HR 核对。"
NO_SPACE = "你的岗位还没有开放可查询的知识空间，请联系 HR。"


class SearchService:
    def __init__(self, settings: Settings, directory: Directory, knowledge: Knowledge) -> None:
        self._s = settings
        self._directory = directory
        self._knowledge = knowledge

    async def search(self, req: SearchRequest) -> SearchResponse:
        top_k = min(req.top_k, self._s.top_k_max)
        profile = await self._directory.get_profile(req.user_id)
        if profile is None or profile.designation is None:
            log.info("search denied: no profile", extra={"user_id": req.user_id})
            return SearchResponse(records=[], notice=NO_PROFILE)
        if not profile.spaces:
            return SearchResponse(records=[], notice=NO_SPACE)

        field = self._s.scope_metadata_field
        tokens = viewer_tokens(profile)
        conditions = metadata_conditions(field, tokens)
        results = await asyncio.gather(
            *(
                self._knowledge.retrieve(sp.dataset_id, req.query, top_k, conditions)
                for sp in profile.spaces
            )
        )

        citations: list[Citation] = []
        dropped = 0
        for space, records in zip(profile.spaces, results, strict=True):
            for r in records:
                if not is_visible(r.metadata, field, tokens):
                    dropped += 1
                    continue
                citations.append(
                    Citation(
                        title=r.title,
                        content=r.content,
                        score=r.score,
                        document_id=r.document_id,
                        space=space.name,
                    )
                )
        if dropped:
            # Dify 过滤没生效或文档缺范围标记，记下来排查；不记录提问内容
            log.warning("dropped %d records outside scope", dropped)
        citations.sort(key=lambda c: c.score, reverse=True)
        return SearchResponse(records=citations[:top_k])
