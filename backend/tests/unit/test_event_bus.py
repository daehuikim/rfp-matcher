from __future__ import annotations

import asyncio

import pytest

from app.domain.enums import PipelineStage
from app.domain.models import PipelineEvent
from app.services.event_bus import EventBus


@pytest.mark.asyncio
async def test_subscriber_receives_published_events_for_doc_id() -> None:
    bus = EventBus()
    doc_id = "doc-1"
    received: list[PipelineEvent] = []

    async def consume() -> None:
        async for ev in bus.subscribe(doc_id):
            received.append(ev)
            if ev.stage == PipelineStage.READY_FOR_REVIEW:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)  # 구독 등록 보장
    await bus.publish(PipelineEvent(doc_id=doc_id, stage=PipelineStage.CONVERTING))
    await bus.publish(PipelineEvent(doc_id="other", stage=PipelineStage.CONVERTING))
    await bus.publish(PipelineEvent(doc_id=doc_id, stage=PipelineStage.READY_FOR_REVIEW))
    await task

    assert [e.stage for e in received] == [
        PipelineStage.CONVERTING,
        PipelineStage.READY_FOR_REVIEW,
    ]


@pytest.mark.asyncio
async def test_wildcard_subscriber_sees_every_doc() -> None:
    bus = EventBus()
    received: list[str] = []

    async def consume() -> None:
        async for ev in bus.subscribe("*"):
            received.append(ev.doc_id)
            if len(received) == 2:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    await bus.publish(PipelineEvent(doc_id="a", stage=PipelineStage.CONVERTING))
    await bus.publish(PipelineEvent(doc_id="b", stage=PipelineStage.CONVERTING))
    await task
    assert received == ["a", "b"]
