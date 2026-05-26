from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.domain.enums import PipelineStage
from app.domain.stage_meta import STAGE_SNIPPET
from app.domain.models import PipelineEvent

if TYPE_CHECKING:
    from app.services.event_bus import EventBus

logger = logging.getLogger(__name__)


class Pipeline:
    """
    파이프라인 단계 전이를 EventBus에 publish하는 얇은 facade.

    모든 emit에 `elapsed_ms`(이전 emit으로부터)와 `elapsed_total_ms`(이 doc의 첫 emit으로부터)를
    자동으로 페이로드에 끼워 넣어 프론트가 실시간 타이머와 단계별 시간을 보여줄 수 있게 한다.
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus
        # doc_id → (first_ts, last_ts) monotonic.  마지막 emit 시각 추적.
        self._timings: dict[str, tuple[float, float]] = {}

    async def emit(
        self,
        doc_id: str,
        stage: PipelineStage,
        payload: dict[str, object] | None = None,
    ) -> None:
        now = time.monotonic()
        first, last = self._timings.get(doc_id, (now, now))
        if doc_id not in self._timings:
            self._timings[doc_id] = (now, now)
        elapsed_ms = int((now - last) * 1000)
        elapsed_total_ms = int((now - first) * 1000)
        self._timings[doc_id] = (first, now)

        merged: dict[str, object] = dict(payload or {})
        merged.setdefault("elapsed_ms", elapsed_ms)
        merged.setdefault("elapsed_total_ms", elapsed_total_ms)
        if "snippet" not in merged and stage in STAGE_SNIPPET:
            merged["snippet"] = STAGE_SNIPPET[stage]

        await self._bus.publish(PipelineEvent(doc_id=doc_id, stage=stage, payload=merged))
        logger.info(
            "stage %s doc=%s Δ=%dms total=%dms %s",
            stage.value,
            doc_id[:8],
            elapsed_ms,
            elapsed_total_ms,
            {k: v for k, v in merged.items() if k not in ("elapsed_ms", "elapsed_total_ms")},
        )

    async def emit_failed(self, doc_id: str, step: PipelineStage, error: str) -> None:
        await self._bus.publish(
            PipelineEvent(
                doc_id=doc_id,
                stage=PipelineStage.FAILED,
                payload={"step": step.value},
                error=error,
            )
        )
