from __future__ import annotations

import logging
from typing import Literal

from app.core.config import Settings
from app.llm.base import AsyncLlmClient
from app.phase2.company_tech.index import CompanyTechIndex
from app.phase2.company_tech.models import InternalReviewResult, QueryRouting
from app.phase2.company_tech.review import generate_technical_review
from app.phase2.company_tech.routing import classify_query_sources

logger = logging.getLogger(__name__)

SearchMode = Literal["Hybrid", "Vector", "BM25"]


async def run_internal_review(
    *,
    query: str,
    index: CompanyTechIndex,
    llm: AsyncLlmClient,
    settings: Settings,
    source_files: list[str] | None = None,
    use_auto_routing: bool | None = None,
    manual_sources: list[str] | None = None,
) -> InternalReviewResult:
    if not query.strip():
        raise ValueError("검토할 요구사항 텍스트가 비어 있습니다.")

    all_sources = source_files or index.source_files()
    use_routing = (
        settings.company_tech_use_auto_routing if use_auto_routing is None else use_auto_routing
    )
    selected_sources = list(manual_sources or [])
    routing = QueryRouting()

    if use_routing:
        routing = await classify_query_sources(
            llm=llm,
            model=settings.company_tech_routing_model,
            query=query,
            source_files=all_sources,
        )
        selected_sources = routing.selected_sources

    search_mode = settings.company_tech_search_mode
    top_k = settings.company_tech_top_k
    api_key = settings.openai_api_key
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 필요합니다 (임베딩·판정).")

    if search_mode == "Hybrid":
        evidence_results = await index.hybrid_search(
            query=query,
            top_k=top_k,
            embedding_model=settings.company_tech_embedding_model,
            api_key=api_key,
            source_files=selected_sources,
        )
    elif search_mode == "Vector":
        evidence_results = await index.vector_search(
            query=query,
            top_k=top_k,
            embedding_model=settings.company_tech_embedding_model,
            api_key=api_key,
            source_files=selected_sources,
        )
    elif search_mode == "BM25":
        evidence_results = await index.bm25_search(
            query=query,
            top_k=top_k,
            source_files=selected_sources,
        )
    else:
        raise ValueError(f"지원하지 않는 search mode: {search_mode}")

    if not evidence_results:
        raise ValueError("검토에 사용할 내부 근거 chunk를 찾지 못했습니다.")

    review = await generate_technical_review(
        llm=llm,
        model=settings.company_tech_review_model,
        query=query,
        selected_sources=selected_sources,
        evidence_results=evidence_results,
    )
    logger.debug(
        "company_tech review status=%s sources=%s evidence=%d",
        review.status,
        selected_sources,
        len(evidence_results),
    )
    return InternalReviewResult(
        review=review,
        routing=routing,
        selected_sources=selected_sources,
        evidence_results=evidence_results,
    )
