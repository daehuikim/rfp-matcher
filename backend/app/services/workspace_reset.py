"""워크스페이스 초기화 — in-memory 세션·storage·artifacts 일괄 삭제."""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.container import Container

logger = logging.getLogger(__name__)


def _clear_directory(path: Path) -> int:
    if not path.is_dir():
        return 0
    removed = 0
    for child in path.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
        removed += 1
    return removed


async def reset_workspace(container: Container) -> dict[str, int]:
    """모든 프로젝트·캐시·디스크 산출물 제거. 카탈로그 BM25 인덱스는 유지."""
    # 실행 중 파이프라인 취소
    for doc_id, task in list(container.pipeline_tasks.items()):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.debug("pipeline cancel doc=%s", doc_id[:8], exc_info=True)
    container.pipeline_tasks.clear()
    container.llm_usage_by_doc.clear()

    await container.repo.clear_all()
    container.event_bus.clear_all()

    settings = container.settings
    storage_n = _clear_directory(settings.storage_root)
    artifact_n = _clear_directory(settings.artifact_cache_dir)

    logger.info(
        "workspace reset storage=%d artifact_buckets=%d",
        storage_n,
        artifact_n,
    )
    return {
        "storage_entries_removed": storage_n,
        "artifact_buckets_removed": artifact_n,
    }
