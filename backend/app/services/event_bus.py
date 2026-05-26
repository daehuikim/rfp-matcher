from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator

from app.domain.models import PipelineEvent

logger = logging.getLogger(__name__)


class EventBus:
    """
    In-process pub/sub on top of asyncio.Queue.

    파이프라인 단계는 polling 없이 이 버스를 통해 다음 단계 시작 신호를 listen한다.
    SSE 어댑터도 동일 버스를 구독해 프론트 진행률에 그대로 흘린다.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[PipelineEvent]]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last: dict[str, PipelineEvent] = {}

    async def publish(self, event: PipelineEvent) -> None:
        self._last[event.doc_id] = event
        async with self._lock:
            queues = list(self._subscribers.get(event.doc_id, []))
            global_queues = list(self._subscribers.get("*", []))
        for q in (*queues, *global_queues):
            await q.put(event)
        logger.debug("publish doc_id=%s stage=%s", event.doc_id, event.stage)

    def last_event(self, doc_id: str) -> PipelineEvent | None:
        return self._last.get(doc_id)

    async def subscribe(self, doc_id: str) -> AsyncIterator[PipelineEvent]:
        queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()
        async with self._lock:
            self._subscribers[doc_id].append(queue)
        try:
            while True:
                ev = await queue.get()
                yield ev
        finally:
            async with self._lock:
                if queue in self._subscribers.get(doc_id, []):
                    self._subscribers[doc_id].remove(queue)
