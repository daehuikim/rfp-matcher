from __future__ import annotations

from app.domain.enums import PipelineStage
from app.services.event_bus import EventBus


def test_export_import_snapshot_preserves_total_ms() -> None:
    bus = EventBus()
    bus.record_emit(
        "doc-1",
        stage=PipelineStage.CONVERTING.value,
        payload={"snippet": "변환"},
    )
    bus.record_emit(
        "doc-1",
        stage=PipelineStage.READY_FOR_REVIEW.value,
        payload={"requirements": 10, "elapsed_total_ms": 125_000},
    )
    # simulate stored timing in last payload
    hist = bus.history("doc-1")
    hist[-1]["payload"]["elapsed_total_ms"] = 125_000

    snapshot = bus.export_snapshot("doc-1")
    assert snapshot["total_elapsed_ms"] == 125_000

    bus2 = EventBus()
    bus2.import_snapshot("doc-2", snapshot)
    assert bus2.total_elapsed_ms("doc-2") == 125_000
    assert len(bus2.history("doc-2")) == len(snapshot["history"])
    last = bus2.last_event("doc-2")
    assert last is not None
    assert last.stage == PipelineStage.READY_FOR_REVIEW
