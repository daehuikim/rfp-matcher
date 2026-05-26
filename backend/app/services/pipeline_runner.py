from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.domain.enums import PipelineStage
from app.services.artifact_cache import ArtifactCache
from app.services.extraction import ExtractionService

if TYPE_CHECKING:
    from app.core.container import Container
    from app.domain.models import Document

logger = logging.getLogger(__name__)

_TERMINAL_STAGES = frozenset(
    {
        PipelineStage.RECOMMENDED.value,
        PipelineStage.FAILED.value,
    }
)


def _task_running(container: Container, doc_id: str) -> bool:
    task = container.pipeline_tasks.get(doc_id)
    return task is not None and not task.done()


async def reset_doc_extraction(container: Container, doc_id: str) -> None:
    """artifacts 수동 삭제 등 — 메모리 추출·AI·파이프라인 상태만 비운다."""
    await container.repo.clear_extraction_data(doc_id)
    container.event_bus.reset_doc(doc_id)
    container.llm_usage_by_doc.pop(doc_id, None)


def _disk_cache_valid(container: Container, document: Document) -> bool:
    if not document.content_hash:
        return False
    cache = ArtifactCache(container.settings.artifact_cache_dir)
    return cache.has_extraction(document.content_hash)


async def schedule_extraction_run(container: Container, document: Document) -> None:
    """업로드·샘플·ensure 엔드포인트에서 idempotent하게 파이프라인 시작."""
    if _task_running(container, document.id):
        logger.debug("pipeline already running doc=%s", document.id[:8])
        return
    task = asyncio.create_task(_run_guarded(container, document))
    container.pipeline_tasks[document.id] = task


async def _run_guarded(container: Container, document: Document) -> None:
    doc_id = document.id
    try:
        await ExtractionService(container).run(document)
    except Exception:
        logger.exception("extraction failed doc=%s", doc_id[:8])
    finally:
        container.pipeline_tasks.pop(doc_id, None)


async def ensure_extraction_pipeline(container: Container, doc_id: str) -> dict[str, str]:
    """
    리뷰 페이지 진입 시 호출 — 캐시 있으면 즉시 복원, 없으면 전체 파이프라인 실행.

    반환 status: missing | running | complete | started | recommend_started
    """
    doc = container.repo.documents.get(doc_id)
    if doc is None:
        return {"status": "missing", "reason": "document 없음"}

    if not doc.src_path.is_file():
        return {"status": "missing", "reason": "원본 파일 없음 — 다시 업로드하세요"}

    if _task_running(container, doc_id):
        return {"status": "running"}

    cache_valid = _disk_cache_valid(container, doc)
    reqs, recs, _ = await container.repo.snapshot(doc_id)
    ev = container.event_bus.last_event(doc_id)
    stage = ev.stage.value if ev else PipelineStage.UPLOADED.value

    if not cache_valid and (len(reqs) > 0 or stage in _TERMINAL_STAGES):
        await reset_doc_extraction(container, doc_id)
        await schedule_extraction_run(container, doc)
        return {"status": "started", "reason": "cache_cleared"}

    if stage in _TERMINAL_STAGES:
        if stage == PipelineStage.FAILED.value:
            await schedule_extraction_run(container, doc)
            return {"status": "started", "reason": "failed_retry"}
        if len(reqs) > 0 and len(recs) >= len(reqs):
            return {"status": "complete"}
        if len(reqs) > 0 and len(recs) < len(reqs):
            await schedule_extraction_run(container, doc)
            return {"status": "recommend_started"}

    if len(reqs) > 0:
        if stage in {
            PipelineStage.READY_FOR_REVIEW.value,
            PipelineStage.RECOMMENDING.value,
        } and len(recs) < len(reqs):
            await schedule_extraction_run(container, doc)
            return {"status": "recommend_started"}
        return {"status": "running"}

    # 요건 없음 — UPLOADED·중단·서버 재시작 후 재개
    await schedule_extraction_run(container, doc)
    return {"status": "started"}
