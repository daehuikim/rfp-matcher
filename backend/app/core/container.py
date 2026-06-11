from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.config import Settings, get_settings
from app.llm.base import AsyncLlmClient
from app.llm.factory import active_llm_model, apply_llm_provider, build_llm_client
from app.llm.usage import LlmUsageTracker
from app.phase1.converters.base import HtmlConverter
from app.phase1.converters.pymupdf_converter import PymupdfConverter
from app.phase1.converters.registry import build_pdf_converter
from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever
from app.storage.repo import InMemoryRepo

if TYPE_CHECKING:
    from app.phase2.company_tech.index import CompanyTechIndex
    from app.services.event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """
    앱 lifespan 동안 단일 인스턴스로 유지되는 무거운 리소스 모음.

    카탈로그 검색은 BM25(bm25s) — OpenAI embedding/Chroma 미사용.
    """

    settings: Settings
    llm: AsyncLlmClient
    event_bus: EventBus
    repo: InMemoryRepo
    catalog_retriever: Bm25CatalogRetriever
    company_tech_index: CompanyTechIndex | None = None
    pdf_converter: HtmlConverter = field(default_factory=PymupdfConverter)
    llm_usage_by_doc: dict[str, LlmUsageTracker] = field(default_factory=dict)
    pipeline_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)

    def llm_tracker(self, doc_id: str) -> LlmUsageTracker:
        if doc_id not in self.llm_usage_by_doc:
            self.llm_usage_by_doc[doc_id] = LlmUsageTracker(
                provider=self.settings.llm_provider,
                model=active_llm_model(self.settings),
            )
        return self.llm_usage_by_doc[doc_id]

    def active_llm_model(self) -> str:
        return active_llm_model(self.settings)

    def set_llm_provider(self, provider: str | None) -> None:
        apply_llm_provider(self, provider)


def build_llm(settings: Settings) -> AsyncLlmClient:
    return build_llm_client(settings)


async def build_container() -> Container:
    from app.phase2.catalog.store import CatalogStore
    from app.services.catalog_indexer import CatalogIndexer
    from app.services.catalog_seed import synthesize_seed_catalog
    from app.services.event_bus import EventBus

    settings = get_settings()
    llm = build_llm(settings)
    event_bus = EventBus()
    repo = InMemoryRepo()
    catalog_retriever = Bm25CatalogRetriever()

    pdf_converter = build_pdf_converter(settings)
    if settings.pdf_converter == "docling":
        try:
            await pdf_converter._get_dc()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            logger.exception("Docling 워밍업 실패 — 첫 요청에서 다시 시도됨")
    elif settings.pdf_converter == "pdf2html":
        import shutil

        if not shutil.which("java"):
            logger.warning(
                "PDF_CONVERTER=pdf2html 이지만 Java 미설치 — "
                "brew install openjdk 또는 PDF_CONVERTER=pymupdf 권장"
            )

    company_tech_index = None
    if settings.recommend_engine == "company_tech":
        from app.phase2.company_tech.index import CompanyTechIndex

        company_tech_index = await CompanyTechIndex.load(settings)
        if company_tech_index is None:
            logger.warning(
                "company_tech 인덱스 미준비 — data/chroma_db 빌드 필요 "
                "(python -m app.preprocessing.build_vectordb)"
            )

    # BM25 인덱스 — 카탈로그 JSON에서 lifespan 시 1회 빌드 (수백 건 → ms)
    if settings.recommend_engine == "catalog" and not catalog_retriever.indexed:
        if settings.catalog_path.exists():
            cat_store = CatalogStore.load(settings.catalog_path)
        else:
            cat_store = CatalogStore(settings.catalog_path)
            cat_store.replace(synthesize_seed_catalog())
        from app.phase2.catalog.canonicalizer import (
            canonicalize_catalog_entries,
            save_catalog_aliases,
        )

        raw_entries = cat_store.entries
        canon = canonicalize_catalog_entries(
            raw_entries,
            near_threshold=settings.catalog_near_duplicate_threshold,
        )
        if canon.removed_count:
            save_catalog_aliases(settings.catalog_aliases_path, canon)
            logger.info(
                "카탈로그 dedup: %d → %d entries (%d merged)",
                len(raw_entries),
                len(canon.entries),
                canon.removed_count,
            )
        catalog_retriever.set_alias_map(canon.alias_map)
        cat_store.replace(canon.entries)

        if cat_store.entries:
            await CatalogIndexer(catalog_retriever).index(cat_store)
        else:
            logger.warning("카탈로그 비어 있음 — AI 매칭 검색 불가")

    tech_chunks = company_tech_index.chunk_count if company_tech_index else 0
    logger.info(
        "Container 초기화 완료 (llm=%s, engine=%s, catalog_bm25=%d, tech_chunks=%d, pdf=%s)",
        settings.llm_provider,
        settings.recommend_engine,
        await catalog_retriever.count(),
        tech_chunks,
        settings.pdf_converter,
    )
    return Container(
        settings=settings,
        llm=llm,
        event_bus=event_bus,
        repo=repo,
        catalog_retriever=catalog_retriever,
        company_tech_index=company_tech_index,
        pdf_converter=pdf_converter,
    )


async def teardown_container(container: Container) -> None:
    logger.info("Container teardown")
