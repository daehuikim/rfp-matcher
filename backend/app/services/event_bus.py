from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.domain.models import PipelineEvent

logger = logging.getLogger(__name__)

_MAX_HISTORY = 250


class EventBus:
    """
    In-process pub/sub on top of asyncio.Queue.

    파이프라인 단계는 polling 없이 이 버스를 통해 다음 단계 시작 신호를 listen한다.
    SSE 어댑터도 동일 버스를 구독해 프론트 진행률에 그대로 흘린다.

    `_timings`·`_history`는 doc_id별로 유지 — Pipeline 인스턴스가 바뀌어도 누적 시간이 유지된다.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[PipelineEvent]]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last: dict[str, PipelineEvent] = {}
        self._timings: dict[str, tuple[float, float]] = {}
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)

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

    def history(self, doc_id: str) -> list[dict[str, Any]]:
        return list(self._history.get(doc_id, []))

    def export_snapshot(self, doc_id: str) -> dict[str, Any]:
        """디스크 캐시·API용 파이프라인 타이밍 스냅샷."""
        history = self.history(doc_id)
        total_ms = 0
        if history:
            last_payload = history[-1].get("payload") or {}
            total_ms = int(last_payload.get("elapsed_total_ms") or 0)
        last = self.last_event(doc_id)
        return {
            "history": history,
            "total_elapsed_ms": total_ms,
            "last_stage": last.stage.value if last else "UPLOADED",
            "recorded_at": datetime.now(UTC).isoformat(),
        }

    def import_snapshot(self, doc_id: str, snapshot: dict[str, Any]) -> None:
        """캐시 복원 — history·타이밍 앵커·last_event 재구성 (실시간 emit 없음)."""
        history = list(snapshot.get("history") or [])
        self._history[doc_id] = history

        total_ms = int(snapshot.get("total_elapsed_ms") or 0)
        if history and total_ms <= 0:
            last_payload = history[-1].get("payload") or {}
            total_ms = int(last_payload.get("elapsed_total_ms") or 0)

        now = time.monotonic()
        if total_ms > 0:
            first = now - total_ms / 1000.0
            self._timings[doc_id] = (first, now)
        elif doc_id not in self._timings:
            self._timings[doc_id] = (now, now)

        if history:
            last_entry = history[-1]
            from app.domain.enums import PipelineStage

            stage_raw = last_entry.get("stage", "UPLOADED")
            try:
                stage = PipelineStage(stage_raw)
            except ValueError:
                stage = PipelineStage.UPLOADED
            payload = dict(last_entry.get("payload") or {})
            self._last[doc_id] = PipelineEvent(doc_id=doc_id, stage=stage, payload=payload)

    def total_elapsed_ms(self, doc_id: str) -> int:
        history = self.history(doc_id)
        if history:
            last_payload = history[-1].get("payload") or {}
            return int(last_payload.get("elapsed_total_ms") or 0)
        first, last = self._timings.get(doc_id, (0.0, 0.0))
        if first and last:
            return int((last - first) * 1000)
        return 0

    def reset_doc(self, doc_id: str) -> None:
        """캐시 무효·재추출 전 파이프라인 진행 상태 초기화."""
        self._last.pop(doc_id, None)
        self._timings.pop(doc_id, None)
        self._history.pop(doc_id, None)

    def record_emit(
        self,
        doc_id: str,
        *,
        stage: str,
        payload: dict[str, Any],
    ) -> tuple[int, int, dict[str, Any]]:
        """타이밍 갱신 + history append. 반환: (elapsed_ms, elapsed_total_ms, stored_payload)."""
        now = time.monotonic()
        first, last = self._timings.get(doc_id, (now, now))
        if doc_id not in self._timings:
            self._timings[doc_id] = (now, now)
            elapsed_ms, elapsed_total_ms = 0, 0
        else:
            elapsed_ms = int((now - last) * 1000)
            elapsed_total_ms = int((now - first) * 1000)
        self._timings[doc_id] = (first, now)

        stored = dict(payload)
        stored["elapsed_ms"] = elapsed_ms
        stored["elapsed_total_ms"] = elapsed_total_ms
        entry = {
            "stage": stage,
            "payload": stored,
            "ts": datetime.now(UTC).isoformat(),
        }
        hist = self._history[doc_id]
        hist.append(entry)
        if len(hist) > _MAX_HISTORY:
            del hist[: len(hist) - _MAX_HISTORY]
        return elapsed_ms, elapsed_total_ms, stored

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
