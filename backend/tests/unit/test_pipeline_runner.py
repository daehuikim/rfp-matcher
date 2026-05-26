from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.container import Container
from app.domain.enums import DocumentMime, PipelineStage
from app.domain.models import Document
from app.services.event_bus import EventBus
from app.services.pipeline_runner import ensure_extraction_pipeline, schedule_extraction_run
from app.storage.repo import InMemoryRepo


@pytest.fixture
def container(tmp_path: Path) -> Container:
    from app.core.config import Settings

    settings = Settings(
        storage_root=tmp_path / "storage",
        artifact_cache_dir=tmp_path / "artifacts",
        raw_data_dir=tmp_path / "raw",
    )
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    src = settings.storage_root / "incoming" / "sample.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-1.4 test")
    return Container(
        settings=settings,
        llm=AsyncMock(),
        event_bus=EventBus(),
        repo=InMemoryRepo(),
        catalog_retriever=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_ensure_starts_when_no_requirements(container: Container, tmp_path: Path) -> None:
    src = container.settings.storage_root / "incoming" / "sample.pdf"
    doc = Document(id="doc1", src_path=src, mime=DocumentMime.PDF, content_hash="abc")
    await container.repo.save_document(doc)

    with patch("app.services.pipeline_runner.ExtractionService") as Svc:
        svc = Svc.return_value
        svc.run = AsyncMock(return_value="doc1")

        result = await ensure_extraction_pipeline(container, "doc1")
        assert result["status"] == "started"
        await asyncio.sleep(0)
        svc.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_idempotent_while_running(container: Container) -> None:
    src = container.settings.storage_root / "incoming" / "sample.pdf"
    doc = Document(id="doc2", src_path=src, mime=DocumentMime.PDF)
    await container.repo.save_document(doc)

    gate = asyncio.Event()

    async def slow_run(_document: Document) -> str:
        await gate.wait()
        return _document.id

    with patch("app.services.pipeline_runner.ExtractionService") as Svc:
        svc = Svc.return_value
        svc.run = slow_run
        await schedule_extraction_run(container, doc)
        result = await ensure_extraction_pipeline(container, "doc2")
        assert result["status"] == "running"
        gate.set()
        await container.pipeline_tasks["doc2"]


@pytest.mark.asyncio
async def test_ensure_complete_when_recommended(container: Container, tmp_path: Path) -> None:
    import json

    from app.services.artifact_cache import CACHE_VERSION

    src = container.settings.storage_root / "incoming" / "sample.pdf"
    content_hash = "a" * 64
    doc = Document(id="doc3", src_path=src, mime=DocumentMime.PDF, content_hash=content_hash)
    await container.repo.save_document(doc)
    from app.domain.models import Requirement, Recommendation
    from app.services.pipeline import Pipeline

    bucket = container.settings.artifact_cache_dir / content_hash[:16]
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / "requirements.json").write_text("[]", encoding="utf-8")
    (bucket / "manifest.json").write_text(
        json.dumps({"cache_version": CACHE_VERSION, "content_hash": content_hash}),
        encoding="utf-8",
    )

    req = Requirement(
        id="r1",
        doc_id="doc3",
        category="A",
        code="A-001",
        name="n",
        detail="d",
    )
    await container.repo.save_requirements("doc3", [req])
    await container.repo.upsert_recommendation(
        Recommendation(requirement_id="r1", ai_risk="O", ai_reason="ok", missing_tech=[]),
    )
    pipeline = Pipeline(container.event_bus)
    await pipeline.emit("doc3", PipelineStage.RECOMMENDED, payload={"recommendations": 1})

    result = await ensure_extraction_pipeline(container, "doc3")
    assert result["status"] == "complete"


@pytest.mark.asyncio
async def test_ensure_restarts_when_disk_cache_cleared(container: Container) -> None:
    src = container.settings.storage_root / "incoming" / "sample.pdf"
    doc = Document(id="doc4", src_path=src, mime=DocumentMime.PDF, content_hash="deadbeef")
    await container.repo.save_document(doc)
    from app.domain.models import Requirement, Recommendation
    from app.services.pipeline import Pipeline

    req = Requirement(
        id="r1",
        doc_id="doc4",
        category="A",
        code="A-001",
        name="n",
        detail="d",
    )
    await container.repo.save_requirements("doc4", [req])
    await container.repo.upsert_recommendation(
        Recommendation(requirement_id="r1", ai_risk="O", ai_reason="ok", missing_tech=[]),
    )
    pipeline = Pipeline(container.event_bus)
    await pipeline.emit("doc4", PipelineStage.RECOMMENDED, payload={"recommendations": 1})

    with patch("app.services.pipeline_runner.ExtractionService") as Svc:
        svc = Svc.return_value
        svc.run = AsyncMock(return_value="doc4")
        result = await ensure_extraction_pipeline(container, "doc4")
        assert result["status"] == "started"
        assert result.get("reason") == "cache_cleared"
        await asyncio.sleep(0)
        svc.run.assert_awaited_once()
