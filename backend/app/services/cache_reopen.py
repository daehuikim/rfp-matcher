from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.domain.models import Document
from app.phase1.loaders.base import EXT_TO_MIME
from app.services.artifact_cache import ArtifactCache
from app.services.document_naming import resolve_document_title
from app.services.extraction import ExtractionService
from app.services.pipeline_runner import schedule_extraction_run
from app.services.pipeline_timing import snapshot_total_ms
from app.services.sample_registry import _nfc, sample_display_name, load_samples_manifest

if TYPE_CHECKING:
    from app.core.container import Container

logger = logging.getLogger(__name__)


def _normalize_hash(value: str) -> str:
    return value.strip().lower()


def _matches_hash(full: str, token: str) -> bool:
    full = _normalize_hash(full)
    token = _normalize_hash(token)
    return full == token or full.startswith(token) or token.startswith(full[:16])


def resolve_source_path(container: Container, content_hash: str, source_name: str | None) -> Path:
    """data/raw 등에서 content_hash에 맞는 원본 파일 탐색."""
    raw_dir = container.settings.raw_data_dir
    candidates: list[Path] = []

    if source_name:
        direct = raw_dir / source_name
        if direct.is_file():
            candidates.append(direct)
        target = _nfc(source_name)
        if raw_dir.is_dir():
            for p in raw_dir.iterdir():
                if p.is_file() and _nfc(p.name) == target:
                    candidates.append(p)

    incoming = container.settings.storage_root / "incoming"
    if incoming.is_dir():
        for p in incoming.iterdir():
            if p.is_file():
                candidates.append(p)

    if raw_dir.is_dir():
        for p in raw_dir.iterdir():
            if p.is_file() and not p.name.startswith("."):
                if p not in candidates:
                    candidates.append(p)

    for path in candidates:
        try:
            if ArtifactCache.file_digest(path) == _normalize_hash(content_hash):
                return path
        except OSError:
            continue

    raise FileNotFoundError(
        f"content_hash={content_hash[:16]}… 원본 파일 없음 (data/raw 확인)"
    )


async def reopen_from_cache(container: Container, content_hash: str) -> Document:
    """디스크 아티팩트 → 새 doc_id로 in-memory 복원 (서버 재시작·사이드바 재진입)."""
    cache = ArtifactCache(container.settings.artifact_cache_dir)
    manifest = cache.load_manifest(content_hash)
    if manifest is None:
        raise ValueError("유효한 추출 캐시 없음")

    full_hash = str(manifest["content_hash"])
    source_name = manifest.get("source_name")
    src = resolve_source_path(container, full_hash, source_name)

    incoming_dir = container.settings.storage_root / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    dest = incoming_dir / f"{uuid.uuid4().hex}{ext}"
    shutil.copy2(src, dest)

    service = ExtractionService(container)
    document = await service.prepare(dest)
    digest = ArtifactCache.file_digest(dest)
    if digest != full_hash:
        logger.warning(
            "reopen digest mismatch expected=%s… actual=%s…",
            full_hash[:16],
            digest[:16],
        )

    _order, labels, _featured = load_samples_manifest(container.settings.samples_manifest_path)
    display = sample_display_name(source_name or src.name, labels) if source_name else src.stem
    document = document.model_copy(
        update={
            "content_hash": full_hash,
            "source_filename": source_name or src.name,
            "display_name": display,
        },
    )
    await container.repo.save_document(document)
    await schedule_extraction_run(container, document)
    logger.info(
        "캐시 재오픈 doc=%s hash=%s… src=%s",
        document.id[:8],
        full_hash[:16],
        source_name or src.name,
    )
    return document


def list_cached_project_summaries(container: Container) -> list[dict[str, Any]]:
    cache = ArtifactCache(container.settings.artifact_cache_dir)
    _order, labels, _featured = load_samples_manifest(container.settings.samples_manifest_path)
    active_hashes = {
        doc.content_hash
        for doc in container.repo.documents.values()
        if doc.content_hash
    }
    out: list[dict[str, Any]] = []
    for item in cache.list_cached_projects():
        content_hash = str(item["content_hash"])
        source_name = item.get("source_name")
        title = (
            sample_display_name(str(source_name), labels)
            if source_name
            else str(item.get("title") or content_hash[:8])
        )
        live_doc_id = None
        live_title = title
        for doc_id, doc in container.repo.documents.items():
            if doc.content_hash and _matches_hash(doc.content_hash, content_hash):
                live_doc_id = doc_id
                live_title = resolve_document_title(doc, manifest_source=str(source_name) if source_name else None)
                break
        snapshot = item.get("pipeline_snapshot") or {}
        total_ms = snapshot_total_ms(snapshot, fallback_history=snapshot.get("history"))
        rec_count = int(item.get("recommendation_count") or 0)
        req_count = int(item.get("requirements_count") or 0)
        has_full_recs = bool(item.get("has_recommendations")) or (
            req_count > 0 and rec_count >= req_count
        )
        stage = str(snapshot.get("last_stage") or "READY_FOR_REVIEW")
        if has_full_recs:
            stage = "RECOMMENDED"
        out.append(
            {
                "content_hash": content_hash,
                "bucket": item["bucket"],
                "source_name": source_name,
                "title": live_title if live_doc_id else title,
                "requirements_count": req_count,
                "recommendation_count": rec_count,
                "has_recommendations": has_full_recs,
                "total_elapsed_ms": total_ms,
                "stage": stage,
                "is_complete": has_full_recs,
                "live_doc_id": live_doc_id,
                "is_live": content_hash in active_hashes,
            }
        )
    out.sort(key=lambda x: str(x.get("source_name") or x["title"]))
    return out
