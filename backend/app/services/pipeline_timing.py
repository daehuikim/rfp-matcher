from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.event_bus import EventBus


def capture_pipeline_snapshot(bus: EventBus, doc_id: str) -> dict[str, Any]:
    """EventBus history → 디스크 캐시용 스냅샷."""
    return bus.export_snapshot(doc_id)


def restore_pipeline_snapshot(bus: EventBus, doc_id: str, snapshot: dict[str, Any] | None) -> bool:
    """캐시 manifest의 pipeline_snapshot을 EventBus에 주입. 성공 시 True."""
    if not snapshot or not snapshot.get("history"):
        return False
    bus.import_snapshot(doc_id, snapshot)
    return True


def snapshot_recorded_at(snapshot: dict[str, Any] | None) -> str | None:
    if not snapshot:
        return None
    return snapshot.get("recorded_at")


def snapshot_total_ms(snapshot: dict[str, Any] | None, *, fallback_history: list[dict[str, Any]] | None = None) -> int:
    if snapshot:
        total = int(snapshot.get("total_elapsed_ms") or 0)
        if total > 0:
            return total
        hist = snapshot.get("history") or []
        if hist:
            last_payload = (hist[-1].get("payload") or {}) if isinstance(hist[-1], dict) else {}
            return int(last_payload.get("elapsed_total_ms") or 0)
    if fallback_history:
        for entry in reversed(fallback_history):
            payload = entry.get("payload") or {}
            total = int(payload.get("elapsed_total_ms") or 0)
            if total > 0:
                return total
    return 0


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
