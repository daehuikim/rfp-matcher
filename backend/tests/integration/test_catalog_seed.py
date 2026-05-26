"""M4 evaluator case로부터 시드 카탈로그를 검증한다."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.phase2.catalog.store import CatalogStore
from app.phase2.embedding.fake_embedding import FakeEmbedding
from app.phase2.vectorstore.in_memory_store import InMemoryVectorStore
from app.services.catalog_indexer import CatalogIndexer

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASE_PATH = PROJECT_ROOT / "evaluator/cases/phase2/catalog/seed.json"


@pytest.mark.integration
def test_seed_catalog_satisfies_m4_thresholds() -> None:
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    catalog_path = PROJECT_ROOT / case["catalog_path"]
    if not catalog_path.exists():
        pytest.skip(
            "카탈로그 미생성 — 먼저 "
            "`python -m app.phase2.crawler.kt_catalog_scraper --seed --out <path>` 실행"
        )

    store = CatalogStore.load(catalog_path)
    expected = case["expected"]

    assert len(store) >= expected["entries_min"]
    if expected.get("unique_ids"):
        assert len({e.id for e in store.entries}) == len(store)
    if expected.get("embedding_text_non_empty"):
        assert all(e.embedding_text.strip() for e in store.entries)

    if expected.get("self_search_top1_same_id"):

        async def run() -> bool:
            idx = CatalogIndexer(FakeEmbedding(64), InMemoryVectorStore())
            await idx.index(store)
            sample = store.entries[0]
            hits = await idx.search(sample.embedding_text, k=1)
            return bool(hits) and hits[0][0] == sample.id

        assert asyncio.run(run())

    required = expected.get("required_major_categories")
    if required:
        present = {e.대분류 for e in store.entries}
        missing = set(required) - present
        assert not missing, f"필수 대분류 누락: {missing}"
