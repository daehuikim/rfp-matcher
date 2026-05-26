from __future__ import annotations

import logging

from app.core.container import Container
from app.domain.enums import PipelineStage
from app.domain.models import Recommendation, Requirement
from app.phase2.recommender.recommender import DEFAULT_BATCH_SIZE, RequirementRecommender
from app.services.pipeline import Pipeline

logger = logging.getLogger(__name__)


class RecommendationService:
    """
    문서 전체 Requirement에 대해 BM25 검색 + LLM 배치 검증·rubric.

    batch_size(기본 10)건씩 LLM 1회 호출 — 96건 → ~10회.
    """

    def __init__(self, container: Container) -> None:
        self._c = container
        self._pipeline = Pipeline(container.event_bus)
        batch_size = getattr(container.settings, "recommend_batch_size", DEFAULT_BATCH_SIZE)
        self._recommender = RequirementRecommender(
            llm=container.llm,
            catalog=container.catalog_retriever,
            batch_size=batch_size,
        )
        self._batch_size = batch_size

    async def recommend_document(self, doc_id: str) -> list[Recommendation]:
        reqs = await self._c.repo.list_requirements(doc_id)
        if not reqs:
            return []
        total = len(reqs)
        await self._pipeline.emit(doc_id, PipelineStage.RECOMMENDING, payload={"total": total})

        results: list[Recommendation] = []
        done = 0
        for start in range(0, total, self._batch_size):
            batch = reqs[start : start + self._batch_size]
            batch_recs = await self._recommender.recommend_batch(batch)
            for rec in batch_recs:
                await self._c.repo.upsert_recommendation(rec)
                results.append(rec)
                done += 1
                req = next(r for r in batch if r.id == rec.requirement_id)
                await self._pipeline.emit(
                    doc_id,
                    PipelineStage.RECOMMENDING,
                    payload={
                        "done": done,
                        "total": total,
                        "snippet": f"AI 검토 {done}/{total} · {(req.detail or req.name)[:40]}…",
                    },
                )
            logger.info("AI 배치 완료 doc=%s %d/%d", doc_id, done, total)

        await self._pipeline.emit(
            doc_id, PipelineStage.RECOMMENDED, payload={"recommendations": len(results)}
        )
        return results
