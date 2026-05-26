from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.container import Container
from app.domain.enums import PipelineStage
from app.domain.models import Document, HtmlDoc
from app.llm.fake_client import FakeLlmClient
from app.phase1.converters.base import HtmlConverter
from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever
from app.services.conversion import ConversionService
from app.services.event_bus import EventBus
from app.storage.repo import InMemoryRepo


class _StubConverter(HtmlConverter):
    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{document.id}.html"
        path.write_text("<html><body><table><tr><td>x</td></tr></table></body></html>")
        return HtmlDoc(doc_id=document.id, html_path=path, table_count=1, paragraph_count=0)


@pytest.mark.asyncio
async def test_conversion_emits_converting_then_converted(tmp_path, monkeypatch) -> None:
    # patch at the usage site (services.conversion), not at definition
    monkeypatch.setattr("app.services.conversion.select_converter", lambda _mime: _StubConverter())

    settings = get_settings()
    settings.storage_root = tmp_path
    container = Container(
        settings=settings,
        llm=FakeLlmClient(),
        event_bus=EventBus(),
        repo=InMemoryRepo(),
        catalog_retriever=Bm25CatalogRetriever(),
    )
    service = ConversionService(container)

    received: list[PipelineStage] = []

    async def collect() -> None:
        async for ev in container.event_bus.subscribe("*"):
            received.append(ev.stage)
            if ev.stage == PipelineStage.CONVERTED:
                break

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.01)  # subscribe 등록 보장

    src = tmp_path / "sample.pdf"
    src.write_bytes(b"%PDF-1.4 stub")
    html_doc = await service.run(src)

    await asyncio.wait_for(task, timeout=1.0)
    assert received == [PipelineStage.CONVERTING, PipelineStage.CONVERTED]
    assert html_doc.table_count == 1
    assert html_doc.html_path.exists()
