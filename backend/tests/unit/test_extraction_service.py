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
from app.services.event_bus import EventBus
from app.services.extraction import ExtractionService
from app.storage.repo import InMemoryRepo
from tests.unit.recommender_helpers import batch_yes_handler


class _MiniTableConverter(HtmlConverter):
    """헤더에 조견표 키워드가 들어간 미니 HTML을 산출 — TableLocator가 픽업하도록."""

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{document.id}.html"
        path.write_text(
            "<html><body><table>"
            "<tr><td>요건 구분</td><td>상세 내용</td></tr>"
            "<tr><td>데이터 수집</td><td>① 원천 시스템(API/DB/파일)에서 데이터를 수집·연계한다\n"
            "② CSV·Excel 등 지원 파일 형식 업로드를 제공한다</td></tr>"
            "</table></body></html>",
            encoding="utf-8",
        )
        return HtmlDoc(doc_id=document.id, html_path=path, table_count=1, paragraph_count=0)


@pytest.mark.asyncio
async def test_extraction_service_runs_full_pipeline(tmp_path) -> None:
    settings = get_settings()
    settings.storage_root = tmp_path
    container = Container(
        settings=settings,
        llm=FakeLlmClient(structured_handler=batch_yes_handler),
        event_bus=EventBus(),
        repo=InMemoryRepo(),
        catalog_retriever=Bm25CatalogRetriever(),
        # 진짜 PDF 파서를 거치지 않고 가짜 표를 즉시 산출 — 파이프라인 후속 단계만 검증.
        pdf_converter=_MiniTableConverter(),
    )
    service = ExtractionService(container)

    src = tmp_path / "sample.pdf"
    src.write_bytes(b"%PDF-1.4 stub")

    received_stages: list[PipelineStage] = []

    async def consume() -> None:
        async for ev in container.event_bus.subscribe("*"):
            received_stages.append(ev.stage)
            if ev.stage == PipelineStage.READY_FOR_REVIEW:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)

    document = await service.prepare(src)
    doc_id = await service.run(document)

    await asyncio.wait_for(task, timeout=2.0)

    # 단계 시퀀스가 기대 순서대로 publish 됐는지 (CLASSIFYING/CLASSIFIED 포함)
    expected_order = [
        PipelineStage.UPLOADED,
        PipelineStage.CONVERTING,
        PipelineStage.CONVERTED,
        PipelineStage.LOCATING,
        PipelineStage.LOCATED,
        PipelineStage.ATOMIZING,
        PipelineStage.ATOMIZED,
        PipelineStage.CLASSIFYING,
        PipelineStage.CLASSIFIED,
        PipelineStage.ATOMIZING,
        PipelineStage.ATOMIZING,
        PipelineStage.READY_FOR_REVIEW,
    ]
    assert received_stages == expected_order

    reqs = await container.repo.list_requirements(doc_id)
    assert len(reqs) == 2  # ①, ② 분해
    # 명시 분류("데이터 수집") 100% coverage → PassThrough → 그대로 사용
    assert reqs[0].category == "데이터 수집"
    assert reqs[0].code.startswith("데이")  # 한글 분류 코드 prefix
