from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.container import Container
from app.domain.enums import DocumentMime, Judgement, PipelineStage
from app.domain.models import Document, Recommendation, Requirement
from app.llm.fake_client import FakeLlmClient
from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever
from app.services.artifact_cache import (
    CACHE_VERSION,
    RECOMMENDATION_CACHE_VERSION,
    ArtifactCache,
)
from app.services.event_bus import EventBus
from app.storage.repo import InMemoryRepo


def _doc(tmp_path: Path, content_hash: str) -> Document:
    src = tmp_path / "sample.pdf"
    src.write_bytes(b"same-bytes")
    return Document(
        id="doc-1",
        src_path=src,
        mime=DocumentMime.PDF,
        content_hash=content_hash,
    )


def _req(doc_id: str, code: str, *, req_id: str = "old-id") -> Requirement:
    return Requirement(
        id=req_id,
        doc_id=doc_id,
        category="데이터 수집",
        code=code,
        name=f"name-{code}",
        detail=f"detail-{code}",
    )


def _rec(req: Requirement) -> Recommendation:
    return Recommendation(
        requirement_id=req.id,
        ai_risk=Judgement.YES,
        ai_reason="ok",
        rubric_scores={"기술적합도": 4.0},
    )


@pytest.mark.asyncio
async def test_save_extraction_preserves_recommendations_when_codes_unchanged(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "artifacts")
    digest = "beef" * 16
    doc = _doc(tmp_path, digest)
    html = tmp_path / "doc.html"
    html.write_text("<html></html>", encoding="utf-8")
    req = _req(doc.id, "데이-001")
    cache.save_extraction(document=doc, requirements=[req], html_path=html)
    cache.save_recommendations(document=doc, recommendations=[_rec(req)])

    cache.save_extraction(document=doc, requirements=[req], html_path=html)
    bucket = cache._bucket(doc.content_hash)
    assert (bucket / "recommendations.json").is_file()
    manifest = json.loads((bucket / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["has_recommendations"] is True
    assert cache.has_recommendations(doc.content_hash) is True


@pytest.mark.asyncio
async def test_save_extraction_invalidates_stale_recommendations(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "artifacts")
    digest = ArtifactCache.file_digest(tmp_path / "x.pdf") if False else "abc" * 10 + "ab"
    doc = _doc(tmp_path, digest[:64])
    html = tmp_path / "doc.html"
    html.write_text("<html></html>", encoding="utf-8")

    reqs = [_req(doc.id, "데이-001", req_id="r1")]
    cache.save_extraction(document=doc, requirements=reqs, html_path=html)
    bucket = cache._bucket(doc.content_hash)
    (bucket / "recommendations.json").write_text('{"legacy": true}', encoding="utf-8")

    cache.save_extraction(document=doc, requirements=reqs, html_path=html)
    assert not (bucket / "recommendations.json").exists()
    manifest = json.loads((bucket / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["has_recommendations"] is False


@pytest.mark.asyncio
async def test_restore_recommendations_remaps_by_code(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "artifacts")
    digest = "deadbeef" * 8
    doc = _doc(tmp_path, digest)
    html = tmp_path / "doc.html"
    html.write_text("<html></html>", encoding="utf-8")

    cached_req = _req(doc.id, "데이-001", req_id="cached-req-id")
    cache.save_extraction(document=doc, requirements=[cached_req], html_path=html)
    cache.save_recommendations(document=doc, recommendations=[_rec(cached_req)])

    settings = get_settings()
    settings.storage_root = tmp_path / "storage"
    container = Container(
        settings=settings,
        llm=FakeLlmClient(),
        event_bus=EventBus(),
        repo=InMemoryRepo(),
        catalog_retriever=Bm25CatalogRetriever(),
    )
    live_req = cached_req.model_copy(update={"id": "live-req-id", "doc_id": doc.id})
    await container.repo.save_requirements(doc.id, [live_req])

    n = await cache.restore_recommendations(container, doc, fast=True)
    assert n == 1
    rec = await container.repo.get_recommendation("live-req-id")
    assert rec is not None
    assert rec.ai_risk == Judgement.YES
    assert rec.rubric_scores["기술적합도"] == 4.0


@pytest.mark.asyncio
async def test_has_recommendations_requires_v2_and_matching_counts(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "artifacts")
    digest = "cafebabe" * 8
    doc = _doc(tmp_path, digest)
    bucket = cache._bucket(doc.content_hash)
    bucket.mkdir(parents=True)
    (bucket / "recommendations.json").write_text("[]", encoding="utf-8")
    assert cache.has_recommendations(doc.content_hash) is False

    html = tmp_path / "doc.html"
    html.write_text("<html></html>", encoding="utf-8")
    req = _req(doc.id, "데이-001")
    cache.save_extraction(document=doc, requirements=[req], html_path=html)
    assert cache.has_recommendations(doc.content_hash) is False

    cache.save_recommendations(document=doc, recommendations=[_rec(req)])
    assert cache.has_recommendations(doc.content_hash) is True

    payload = json.loads((bucket / "recommendations.json").read_text(encoding="utf-8"))
    assert payload["recommendation_cache_version"] == RECOMMENDATION_CACHE_VERSION


@pytest.mark.asyncio
async def test_merge_save_recommendations_accumulates_partial(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "artifacts")
    digest = "abc123" * 8
    doc = _doc(tmp_path, digest)
    html = tmp_path / "doc.html"
    html.write_text("<html></html>", encoding="utf-8")
    req1 = _req(doc.id, "데이-001", req_id="r1")
    req2 = _req(doc.id, "데이-002", req_id="r2")
    cache.save_extraction(document=doc, requirements=[req1, req2], html_path=html)

    cache.merge_save_recommendations(document=doc, recommendations=[_rec(req1)])
    assert cache.count_cached_recommendations(doc.content_hash) == 1
    assert cache.has_recommendations(doc.content_hash) is False

    cache.merge_save_recommendations(document=doc, recommendations=[_rec(req2)])
    assert cache.count_cached_recommendations(doc.content_hash) == 2
    assert cache.has_recommendations(doc.content_hash) is True


@pytest.mark.asyncio
async def test_restore_full_emits_recommended(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "artifacts")
    digest = "feedface" * 8
    doc = _doc(tmp_path, digest)
    html = tmp_path / "doc.html"
    html.write_text("<html></html>", encoding="utf-8")
    req = _req(doc.id, "데이-001")
    cache.save_extraction(document=doc, requirements=[req], html_path=html)
    cache.save_recommendations(document=doc, recommendations=[_rec(req)])

    settings = get_settings()
    settings.storage_root = tmp_path / "storage"
    container = Container(
        settings=settings,
        llm=FakeLlmClient(),
        event_bus=EventBus(),
        repo=InMemoryRepo(),
        catalog_retriever=Bm25CatalogRetriever(),
    )

    stages: list[PipelineStage] = []

    async def consume() -> None:
        async for ev in container.event_bus.subscribe(doc.id):
            stages.append(ev.stage)
            if ev.stage == PipelineStage.RECOMMENDED:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    await cache.restore_full(container, doc)
    await asyncio.wait_for(task, timeout=2.0)

    assert PipelineStage.READY_FOR_REVIEW in stages
    assert PipelineStage.RECOMMENDED in stages
    reqs, recs, _ = await container.repo.snapshot(doc.id)
    assert len(reqs) == 1
    assert len(recs) == 1
