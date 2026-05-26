from __future__ import annotations

import re

import pytest
from pydantic import BaseModel

from app.domain.models import Requirement
from app.llm.base import Message
from app.llm.fake_client import FakeLlmClient
from app.phase2.catalog.store import CatalogStore
from app.phase2.recommender.recommender import RequirementRecommender
from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever
from app.services.catalog_indexer import CatalogIndexer
from app.services.catalog_seed import synthesize_seed_catalog


@pytest.mark.asyncio
async def test_batch_retry_when_llm_returns_partial_results(tmp_path) -> None:
    """배치 3건 중 1건만 반환해도 개별 재시도로 전부 채운다."""
    store = CatalogStore(tmp_path / "cat.json")
    store.replace(synthesize_seed_catalog())
    retriever = Bm25CatalogRetriever()
    await CatalogIndexer(retriever).index(store)

    batch_calls: list[int] = []

    def flaky_handler(schema: type[BaseModel], msgs: list[Message]) -> BaseModel:
        ids = re.findall(r"requirement_id=([^\s\n]+)", msgs[0].content if msgs else "")
        batch_calls.append(len(ids))
        # 배치 3건이면 첫 id만 반환 (누락 시뮬레이션)
        if len(ids) > 1:
            ids = ids[:1]
        return schema.model_validate(
            {
                "results": [
                    {
                        "batch_index": 0,
                        "requirement_id": rid,
                        "verified_ids": [],
                        "excluded_notes": [],
                        "rubric": {
                            "기술적합도": 2,
                            "데이터요건": 2,
                            "컴플라이언스": 2,
                            "레퍼런스": 2,
                            "컨소시엄": 2,
                        },
                        "reason": "ok",
                        "missing_tech": [],
                        "consortium_need": None,
                    }
                    for rid in ids
                ]
            }
        )

    reqs = [
        Requirement(
            id=f"req-{i:032d}",
            doc_id="d",
            category="데이터",
            code=f"C-{i}",
            name=f"n{i}",
            detail=f"요구사항 본문 {i}",
        )
        for i in range(3)
    ]
    recs = await RequirementRecommender(
        FakeLlmClient(structured_handler=flaky_handler),
        retriever,
    ).recommend_batch(reqs)

    assert len(recs) == 3
    assert all("AI 배치 응답 누락" not in r.ai_reason for r in recs)
    assert batch_calls[0] == 3
    assert all(c == 1 for c in batch_calls[1:])
