from __future__ import annotations

import pytest

from app.phase2.catalog.store import CatalogStore
from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever
from app.services.catalog_indexer import CatalogIndexer
from app.services.catalog_seed import synthesize_seed_catalog


@pytest.mark.asyncio
async def test_seed_catalog_covers_k_intelligence_suite_categories() -> None:
    entries = synthesize_seed_catalog()
    assert len(entries) >= 30, f"시드 부족: {len(entries)}건"
    assert len({e.id for e in entries}) == len(entries)
    assert all(e.embedding_text.strip() for e in entries)
    majors = {e.대분류 for e in entries}
    assert {"K Model", "K RAG", "K Agent", "K Studio", "K RAI", "K SPC"} <= majors


@pytest.mark.asyncio
async def test_indexer_upserts_and_search_returns_topk(tmp_path) -> None:
    store = CatalogStore(tmp_path / "cat.json")
    store.replace(synthesize_seed_catalog())
    retriever = Bm25CatalogRetriever()
    indexer = CatalogIndexer(retriever)
    n = await indexer.index(store)
    assert n == len(store)

    sample = store.entries[0]
    hits = await indexer.search(sample.embedding_text, k=3)
    assert hits[0][0] == sample.id
    assert hits[0][1] > 0


@pytest.mark.asyncio
async def test_singleton_handles_are_reused(tmp_path) -> None:
    """같은 retriever 핸들이 여러 indexer에서 공유되면 count가 유지된다."""
    retriever = Bm25CatalogRetriever()
    indexer1 = CatalogIndexer(retriever)
    indexer2 = CatalogIndexer(retriever)
    store = CatalogStore(tmp_path / "cat.json")
    store.replace(synthesize_seed_catalog()[:5])

    await indexer1.index(store)
    assert await retriever.count() == 5
    await indexer2.index(store)
    assert await retriever.count() == 5
