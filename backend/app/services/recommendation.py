from __future__ import annotations

import logging

from app.core.container import Container
from app.domain.enums import PipelineStage
from app.domain.models import Recommendation, Requirement
from app.phase2.recommender.recommender import DEFAULT_BATCH_SIZE
from app.services.pipeline import Pipeline
from app.services.recommendation_factory import build_batch_recommender

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
        self._batch_size = batch_size

    async def recommend_document(self, doc_id: str) -> list[Recommendation]:
        reqs = await self._c.repo.list_requirements(doc_id)
        return await self.recommend_requirements(doc_id, reqs)

    async def recommend_requirements(
        self,
        doc_id: str,
        reqs: list[Requirement],
    ) -> list[Recommendation]:
        if not reqs:
            return []
        doc = self._c.repo.documents.get(doc_id)
        _, existing_recs, _ = await self._c.repo.snapshot(doc_id)
        pending = [r for r in reqs if r.id not in existing_recs]
        if not pending:
            return []

        total = len(await self._c.repo.list_requirements(doc_id))
        done_before = len(existing_recs)
        tracker = self._c.llm_tracker(doc_id)
        recommender = build_batch_recommender(self._c, tracker)
        await self._pipeline.emit(
            doc_id,
            PipelineStage.RECOMMENDING,
            payload={"done": done_before, "total": total},
        )

        results: list[Recommendation] = list(existing_recs.values())
        done = done_before
        cache = None
        if doc and doc.content_hash:
            from app.services.artifact_cache import ArtifactCache

            cache = ArtifactCache(self._c.settings.artifact_cache_dir)

        for start in range(0, len(pending), self._batch_size):
            batch = pending[start : start + self._batch_size]
            batch_recs = await recommender.recommend_batch(batch)
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
            if cache and doc:
                cache.merge_save_recommendations(
                    document=doc,
                    recommendations=batch_recs,
                    event_bus=self._c.event_bus,
                )
            logger.info("AI 배치 완료 doc=%s %d/%d", doc_id, done, total)

        await self._pipeline.emit(
            doc_id,
            PipelineStage.RECOMMENDED,
            payload={
                "recommendations": done,
                "llm_usage": tracker.to_dict(),
                "llm_model": self._c.active_llm_model(),
                "llm_provider": self._c.settings.llm_provider,
            },
        )
        return results
