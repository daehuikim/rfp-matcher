from __future__ import annotations

import logging

from app.phase2.catalog.store import CatalogStore
from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever

logger = logging.getLogger(__name__)


class CatalogIndexer:
    """카탈로그 → BM25 인덱스 (embedding/Chroma 미사용)."""

    def __init__(self, retriever: Bm25CatalogRetriever) -> None:
        self._retriever = retriever

    async def index(self, store: CatalogStore) -> int:
        n = await self._retriever.rebuild(store.entries)
        logger.info("카탈로그 BM25 인덱싱 완료: %d entries", n)
        return n

    async def search(self, query: str, k: int = 10) -> list[tuple[str, float, dict[str, str]]]:
        hits = await self._retriever.search(query, k=k)
        return [(h.id, h.score, h.metadata) for h in hits]
