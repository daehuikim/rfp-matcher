"""동시 편집 시나리오: 두 사용자가 같은 doc을 편집할 때
한쪽의 변경이 EventBus에 publish되고 다른 쪽이 SSE로 수신함을 검증."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.container import Container
from app.domain.enums import DocumentMime, Judgement, PipelineStage
from app.domain.models import Document, Requirement
from app.llm.fake_client import FakeLlmClient
from app.phase2.retrieval.bm25_catalog import Bm25CatalogRetriever
from app.services.event_bus import EventBus
from app.storage.repo import InMemoryRepo


@pytest.mark.asyncio
async def test_two_subscribers_both_receive_judgement_event(tmp_path: Path) -> None:
    """동일 doc_id를 구독하는 두 클라이언트(=두 사용자 탭)가 같은 이벤트를 받는다."""
    settings = get_settings()
    container = Container(
        settings=settings,
        llm=FakeLlmClient(),
        event_bus=EventBus(),
        repo=InMemoryRepo(),
        catalog_retriever=Bm25CatalogRetriever(),
    )
    # 시드: 1 doc + 1 requirement
    await container.repo.save_document(
        Document(id="d1", src_path=tmp_path / "x.pdf", mime=DocumentMime.PDF)
    )
    await container.repo.save_requirements(
        "d1",
        [
            Requirement(
                id="r1",
                doc_id="d1",
                category="데이터",
                code="D-001",
                name="요건",
                detail="본문",
            )
        ],
    )

    # 두 구독자 — 알리스(편집자) & 밥(리뷰어)
    received_alice: list[PipelineStage] = []
    received_bob: list[PipelineStage] = []

    async def consume_alice() -> None:
        async for ev in container.event_bus.subscribe("d1"):
            received_alice.append(ev.stage)
            if ev.stage == PipelineStage.JUDGEMENT_UPDATED:
                break

    async def consume_bob() -> None:
        async for ev in container.event_bus.subscribe("d1"):
            received_bob.append(ev.stage)
            if ev.stage == PipelineStage.JUDGEMENT_UPDATED:
                break

    t_a = asyncio.create_task(consume_alice())
    t_b = asyncio.create_task(consume_bob())
    await asyncio.sleep(0.01)  # 구독 등록 보장

    # 알리스가 PATCH (API 핸들러를 직접 호출하지 않고 EventBus만 검증)
    from app.api.requirements import JudgementPatch, update_judgement

    await update_judgement(
        "r1",
        JudgementPatch(mark=Judgement.YES, note="OK"),
        container,
        x_editor_id="alice-editor-id",
    )

    await asyncio.wait_for(asyncio.gather(t_a, t_b), timeout=1.0)
    assert received_alice == [PipelineStage.JUDGEMENT_UPDATED]
    assert received_bob == [PipelineStage.JUDGEMENT_UPDATED]


@pytest.mark.asyncio
async def test_event_payload_contains_editor_id_for_echo_filtering(tmp_path: Path) -> None:
    settings = get_settings()
    container = Container(
        settings=settings,
        llm=FakeLlmClient(),
        event_bus=EventBus(),
        repo=InMemoryRepo(),
        catalog_retriever=Bm25CatalogRetriever(),
    )
    await container.repo.save_document(
        Document(id="d2", src_path=tmp_path / "x.pdf", mime=DocumentMime.PDF)
    )
    await container.repo.save_requirements(
        "d2",
        [
            Requirement(
                id="r2",
                doc_id="d2",
                category="데이터",
                code="D-001",
                name="요건",
                detail="본문",
            )
        ],
    )

    payloads: list[dict] = []

    async def consume() -> None:
        async for ev in container.event_bus.subscribe("d2"):
            if ev.stage == PipelineStage.JUDGEMENT_UPDATED:
                payloads.append(ev.payload)
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)

    from app.api.requirements import JudgementPatch, update_judgement

    await update_judgement(
        "r2",
        JudgementPatch(mark=Judgement.PARTIAL, note="확인 필요"),
        container,
        x_editor_id="user-42",
    )
    await asyncio.wait_for(task, timeout=1.0)

    assert len(payloads) == 1
    p = payloads[0]
    assert p["requirement_id"] == "r2"
    assert p["mark"] == "△"
    assert p["note"] == "확인 필요"
    assert p["editor_id"] == "user-42"
    # ts는 ISO8601 형식 — 파싱 가능해야 함
    assert "T" in p["ts"]


def test_sse_endpoint_streams_judgement_updates(tmp_path: Path) -> None:
    """전체 API 경로 — SSE 스트림에서 JUDGEMENT_UPDATED 이벤트 수신."""
    import threading
    import time

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        container = app.state.container

        async def seed() -> None:
            await container.repo.save_document(
                Document(id="d-sse", src_path=tmp_path / "x.pdf", mime=DocumentMime.PDF)
            )
            await container.repo.save_requirements(
                "d-sse",
                [
                    Requirement(
                        id="r-sse",
                        doc_id="d-sse",
                        category="A",
                        code="A-1",
                        name="n",
                        detail="d",
                    )
                ],
            )

        asyncio.run(seed())

        chunks: list[str] = []

        def reader() -> None:
            with client.stream("GET", "/documents/d-sse/events") as resp:
                for line in resp.iter_lines():
                    chunks.append(line)
                    if "JUDGEMENT_UPDATED" in line and any(
                        '"requirement_id": "r-sse"' in c for c in chunks
                    ):
                        return

        th = threading.Thread(target=reader, daemon=True)
        th.start()
        time.sleep(0.1)  # SSE 구독 등록 시간 확보

        r = client.patch(
            "/requirements/r-sse/judgement",
            json={"mark": "O", "note": "ok"},
            headers={"X-Editor-Id": "tester"},
        )
        assert r.status_code == 200
        th.join(timeout=2.0)
        joined = "\n".join(chunks)
        assert "JUDGEMENT_UPDATED" in joined
        assert "r-sse" in joined
        assert "tester" in joined
        assert json.dumps
