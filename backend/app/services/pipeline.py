from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.domain.enums import PipelineStage
from app.domain.models import PipelineEvent
from app.domain.stage_meta import STAGE_SNIPPET

if TYPE_CHECKING:
    from app.services.event_bus import EventBus

logger = logging.getLogger(__name__)


class Pipeline:
    """
    파이프라인 단계 전이를 EventBus에 publish하는 얇은 facade.

    elapsed_ms / elapsed_total_ms / history 는 EventBus에 doc_id별로 누적된다.
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus

    async def emit(
        self,
        doc_id: str,
        stage: PipelineStage,
        payload: dict[str, object] | None = None,
    ) -> None:
        merged: dict[str, object] = dict(payload or {})
        if "snippet" not in merged and stage in STAGE_SNIPPET:
            merged["snippet"] = STAGE_SNIPPET[stage]

        elapsed_ms, elapsed_total_ms, merged = self._bus.record_emit(
            doc_id,
            stage=stage.value,
            payload=merged,
        )

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
        _, _, payload = self._bus.record_emit(
            doc_id,
            stage=PipelineStage.FAILED.value,
            payload={"step": step.value},
        )
        await self._bus.publish(
            PipelineEvent(
                doc_id=doc_id,
                stage=PipelineStage.FAILED,
                payload=payload,
                error=error,
            )
        )
