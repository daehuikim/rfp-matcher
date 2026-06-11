from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.core.config import Settings
from app.domain.models import Recommendation, Requirement
from app.llm.base import AsyncLlmClient
from app.phase2.company_tech.adapter import to_recommendation
from app.phase2.company_tech.index import CompanyTechIndex
from app.phase2.company_tech.pipeline import run_internal_review

if TYPE_CHECKING:
    from app.llm.usage import LlmUsageTracker

logger = logging.getLogger(__name__)


class CompanyTechRecommender:
    """Chroma Hybrid 검색 + LLM 기술 판정 → Recommendation."""

    def __init__(
        self,
        index: CompanyTechIndex,
        llm: AsyncLlmClient,
        settings: Settings,
        *,
        tracker: LlmUsageTracker | None = None,
        concurrency: int = 3,
    ) -> None:
        self._index = index
        self._llm = llm
        self._settings = settings
        self._tracker = tracker
        self._sem = asyncio.Semaphore(max(1, concurrency))

    async def recommend(self, req: Requirement) -> Recommendation:
        results = await self.recommend_batch([req])
        return results[0]

    async def recommend_batch(self, reqs: list[Requirement]) -> list[Recommendation]:
        if not reqs:
            return []

        async def _one(req: Requirement) -> Recommendation:
            async with self._sem:
                query = (req.detail or req.name or "").strip()
                try:
                    internal = await run_internal_review(
                        query=query,
                        index=self._index,
                        llm=self._llm,
                        settings=self._settings,
                    )
                    return to_recommendation(req.id, internal, query=query)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "company_tech 판정 실패 requirement_id=%s: %s",
                        req.id,
                        exc,
                    )
                    from app.domain.enums import Judgement
                    from app.domain.models import Recommendation as Rec

                    return Rec(
                        requirement_id=req.id,
                        ai_risk=Judgement.PARTIAL,
                        ai_reason=f"검토 중 오류 발생: {exc}",
                        missing_tech=[],
                    )

        return list(await asyncio.gather(*[_one(r) for r in reqs]))
