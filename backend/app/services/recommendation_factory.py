from __future__ import annotations

import logging
from typing import Protocol

from app.core.container import Container
from app.domain.models import Recommendation, Requirement
from app.llm.usage import LlmUsageTracker
from app.phase2.company_tech.recommender import CompanyTechRecommender
from app.phase2.recommender.recommender import DEFAULT_BATCH_SIZE, RequirementRecommender

logger = logging.getLogger(__name__)


class BatchRecommender(Protocol):
    async def recommend_batch(self, reqs: list[Requirement]) -> list[Recommendation]: ...


def build_batch_recommender(container: Container, tracker: LlmUsageTracker) -> BatchRecommender:
    settings = container.settings
    batch_size = getattr(settings, "recommend_batch_size", DEFAULT_BATCH_SIZE)

    if settings.recommend_engine == "company_tech" and container.company_tech_index is not None:
        logger.info("추천 엔진: company_tech (Chroma Hybrid)")
        return CompanyTechRecommender(
            index=container.company_tech_index,
            llm=container.llm,
            settings=settings,
            tracker=tracker,
            concurrency=min(batch_size, settings.llm_concurrency),
        )

    if settings.recommend_engine == "company_tech":
        logger.warning("company_tech_index 없음 — catalog BM25로 폴백")

    return RequirementRecommender(
        llm=container.llm,
        catalog=container.catalog_retriever,
        batch_size=batch_size,
        tracker=tracker,
    )
