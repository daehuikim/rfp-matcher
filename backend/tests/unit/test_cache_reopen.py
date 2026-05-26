from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.container import Container
from app.domain.enums import DocumentMime
from app.services.artifact_cache import ArtifactCache, CACHE_VERSION
from app.services.cache_reopen import list_cached_project_summaries, reopen_from_cache, resolve_source_path
from app.services.event_bus import EventBus
from app.storage.repo import InMemoryRepo


@pytest.fixture
def container(tmp_path: Path) -> Container:
    from app.core.config import Settings

    raw = tmp_path / "raw"
    raw.mkdir()
    src = raw / "sample.pdf"
    src.write_bytes(b"%PDF-1.4 cached sample")

    settings = Settings(
        storage_root=tmp_path / "storage",
        artifact_cache_dir=tmp_path / "artifacts",
        raw_data_dir=raw,
    )
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    return Container(
        settings=settings,
        llm=AsyncMock(),
        event_bus=EventBus(),
        repo=InMemoryRepo(),
        catalog_retriever=AsyncMock(),
    )


def _seed_cache(container: Container, src: Path) -> str:
    content_hash = ArtifactCache.file_digest(src)
    bucket = container.settings.artifact_cache_dir / content_hash[:16]
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / "converted.html").write_text("<html></html>", encoding="utf-8")
    (bucket / "requirements.json").write_text("[]", encoding="utf-8")
    manifest = {
        "cache_version": CACHE_VERSION,
        "content_hash": content_hash,
        "source_name": src.name,
        "mime": "application/pdf",
        "requirements_count": 0,
        "has_recommendations": False,
        "pipeline_snapshot": {
            "history": [{"stage": "READY_FOR_REVIEW", "payload": {"elapsed_total_ms": 1200}, "ts": "t"}],
            "total_elapsed_ms": 1200,
            "last_stage": "READY_FOR_REVIEW",
        },
    }
    import json

    (bucket / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return content_hash


def test_resolve_source_path_by_name(container: Container) -> None:
    src = container.settings.raw_data_dir / "sample.pdf"
    digest = ArtifactCache.file_digest(src)
    found = resolve_source_path(container, digest, "sample.pdf")
    assert found == src


def test_list_cached_projects(container: Container) -> None:
    src = container.settings.raw_data_dir / "sample.pdf"
    digest = _seed_cache(container, src)
    rows = list_cached_project_summaries(container)
    assert len(rows) == 1
    assert rows[0]["content_hash"] == digest


@pytest.mark.asyncio
async def test_reopen_from_cache(container: Container) -> None:
    src = container.settings.raw_data_dir / "sample.pdf"
    digest = _seed_cache(container, src)

    with patch("app.services.cache_reopen.schedule_extraction_run", new_callable=AsyncMock) as sched:
        doc = await reopen_from_cache(container, digest)
        assert doc.id in container.repo.documents
        assert doc.content_hash == digest
        sched.assert_awaited_once()
