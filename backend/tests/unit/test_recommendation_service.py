from __future__ import annotations

import asyncio
import re

import pytest
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.container import Container
from app.domain.enums import Judgement, PipelineStage
from app.domain.models import Document, Requirement
from app.llm.base import Message
from app.llm.fake_client import FakeLlmClient
from app.phase2.catalog.store import CatalogStore
from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever
from app.services.catalog_indexer import CatalogIndexer
from app.services.catalog_seed import synthesize_seed_catalog
from app.services.event_bus import EventBus
from app.services.recommendation import RecommendationService
from app.storage.repo import InMemoryRepo


def _batch_yes(schema: type[BaseModel], msgs: list[Message]) -> BaseModel:
    ids = re.findall(r"requirement_id=([^\s\n]+)", msgs[0].content if msgs else "")
    return schema.model_validate(
        {
            "results": [
                {
                    "requirement_id": rid,
                    "verified_ids": [],
                    "excluded_notes": [],
                    "rubric": {
                        "기술적합도": 1,
                        "데이터요건": 2,
                        "컴플라이언스": 2,
                        "레퍼런스": 2,
                        "컨소시엄": 1,
                    },
                    "reason": "ok",
                    "missing_tech": [],
                    "consortium_need": None,
                }
                for rid in ids
            ]
        }
    )


@pytest.mark.asyncio
async def test_recommend_document_runs_in_batches_and_publishes_progress(tmp_path) -> None:
    settings = get_settings()
    settings.recommend_batch_size = 3
    repo = InMemoryRepo()
    retriever = Bm25CatalogRetriever()
    cat = CatalogStore(tmp_path / "cat.json")
    cat.replace(synthesize_seed_catalog())
    await CatalogIndexer(retriever).index(cat)

    doc = Document(
        id="d1",
        src_path=tmp_path / "x.pdf",
        mime="application/pdf",  # type: ignore[arg-type]
    )
    await repo.save_document(doc)
    reqs = [
        Requirement(
            id=f"r{i:032x}",
            doc_id="d1",
            category="데이터",
            code=f"D-{i:03d}",
            name=f"요건{i}",
            detail=f"요건 {i} 본문",
        )
        for i in range(6)
    ]
    await repo.save_requirements("d1", reqs)

    container = Container(
        settings=settings,
        llm=FakeLlmClient(structured_handler=_batch_yes),
        event_bus=EventBus(),
        repo=repo,
        catalog_retriever=retriever,
    )

    progress: list[PipelineStage] = []

    async def consume() -> None:
        async for ev in container.event_bus.subscribe("d1"):
            progress.append(ev.stage)
            if ev.stage == PipelineStage.RECOMMENDED:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    results = await RecommendationService(container).recommend_document("d1")
    await asyncio.wait_for(task, timeout=2.0)

    assert len(results) == 6
    assert all(r.ai_risk == Judgement.YES for r in results)
    assert progress[0] == PipelineStage.RECOMMENDING
    assert progress[-1] == PipelineStage.RECOMMENDED
    assert progress.count(PipelineStage.RECOMMENDING) >= 7
