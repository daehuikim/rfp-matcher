from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.domain.models import ExtractionMetadata
from app.phase1.extraction.category_provenance import (
    document_category_spec,
    has_inferred_categories,
    summarize_category_sources,
)
from app.phase1.loaders.base import EXT_TO_MIME
from app.services.document_naming import resolve_document_title
from app.services.cache_reopen import list_cached_project_summaries, reopen_from_cache
from app.services.extraction import ExtractionService
from app.services.pipeline_runner import ensure_extraction_pipeline, schedule_extraction_run
from app.services.sample_registry import (
    load_samples_manifest,
    resolve_sample_path,
    sample_display_name,
    sort_sample_names,
    _nfc,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


class UploadResponse(BaseModel):
    doc_id: str
    status: str


class SampleFile(BaseModel):
    name: str
    size_bytes: int
    ext: str
    display: str  # 사람이 보기 좋은 표시명
    featured: bool = False  # 홈 2×3 그리드 노출


class FromSampleRequest(BaseModel):
    name: str


class WorkspaceSessionSummary(BaseModel):
    """사이드바·멀티 프로젝트 전환용 세션 요약."""

    doc_id: str
    title: str
    source_filename: str | None = None
    display_name: str | None = None
    content_hash: str | None = None
    stage: str
    requirements_count: int
    ai_done: int
    ai_total: int
    total_elapsed_ms: int
    is_complete: bool
    updated_at: str | None = None


class CachedProjectSummary(BaseModel):
    content_hash: str
    bucket: str
    source_name: str | None = None
    title: str
    requirements_count: int
    recommendation_count: int
    has_recommendations: bool
    total_elapsed_ms: int
    stage: str
    is_complete: bool
    live_doc_id: str | None = None
    is_live: bool = False


class ReopenCacheRequest(BaseModel):
    content_hash: str


class ExtractionProfileResponse(BaseModel):
    spec: str
    has_requirement_category_column: bool
    atomization_strategy: str
    category_column_header: str | None = None
    has_inferred_categories: bool
    category_source_counts: dict[str, int]


@router.get("/{doc_id}/extraction-profile", response_model=ExtractionProfileResponse)
async def get_extraction_profile(doc_id: str, container: ContainerDep) -> ExtractionProfileResponse:
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    doc = container.repo.documents[doc_id]
    meta: ExtractionMetadata | None = doc.extraction_meta
    reqs, _, _ = await container.repo.snapshot(doc_id)
    return ExtractionProfileResponse(
        spec=document_category_spec(meta, reqs),
        has_requirement_category_column=bool(meta and meta.has_requirement_category_column),
        atomization_strategy=(meta.atomization_strategy if meta else "unknown"),
        category_column_header=meta.category_column_header if meta else None,
        has_inferred_categories=has_inferred_categories(reqs),
        category_source_counts=summarize_category_sources(reqs),
    )


@router.get("/samples", response_model=list[SampleFile])
async def list_samples(container: ContainerDep) -> list[SampleFile]:
    """data/raw/에 비치된 샘플 RFP 파일 목록을 반환. 로컬 PoC용."""
    raw_dir = container.settings.raw_data_dir
    if not raw_dir.exists():
        return []
    order, labels, featured = load_samples_manifest(container.settings.samples_manifest_path)
    featured_set = set(featured)
    found: list[SampleFile] = []
    for p in sorted(raw_dir.iterdir()):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext not in EXT_TO_MIME:
            continue
        found.append(
            SampleFile(
                name=p.name,
                size_bytes=p.stat().st_size,
                ext=ext.lstrip("."),
                display=sample_display_name(p.name, labels),
                featured=_nfc(p.name) in featured_set,
            )
        )
    by_name = {s.name: s for s in found}
    ordered_names = sort_sample_names(list(by_name.keys()), order)
    return [by_name[n] for n in ordered_names]


@router.get("/sessions", response_model=list[WorkspaceSessionSummary])
async def list_workspace_sessions(container: ContainerDep) -> list[WorkspaceSessionSummary]:
    """서버에 등록된 문서(프로젝트) 목록 — 진행 중·완료 상태 포함."""
    out: list[WorkspaceSessionSummary] = []
    for doc_id, doc in container.repo.documents.items():
        reqs, recs, _ = await container.repo.snapshot(doc_id)
        ev = container.event_bus.last_event(doc_id)
        stage = ev.stage.value if ev else "UPLOADED"
        total_ms = container.event_bus.total_elapsed_ms(doc_id)
        ai_total = len(reqs)
        ai_done = len(recs)
        updated_at = None
        history = container.event_bus.history(doc_id)
        if history:
            updated_at = history[-1].get("ts")
        title = resolve_document_title(doc)
        if len(title) > 40:
            title = title[:37] + "…"
        out.append(
            WorkspaceSessionSummary(
                doc_id=doc_id,
                title=title,
                source_filename=doc.source_filename,
                display_name=doc.display_name,
                content_hash=doc.content_hash,
                stage=stage,
                requirements_count=len(reqs),
                ai_done=ai_done,
                ai_total=ai_total,
                total_elapsed_ms=total_ms,
                is_complete=stage == "RECOMMENDED",
                updated_at=updated_at,
            )
        )
    out.sort(key=lambda s: s.updated_at or "", reverse=True)
    return out


@router.get("/cached-projects", response_model=list[CachedProjectSummary])
async def list_cached_projects(container: ContainerDep) -> list[CachedProjectSummary]:
    """data/artifacts 디스크 캐시 — 서버 재시작 후에도 사이드바에 노출."""
    rows = list_cached_project_summaries(container)
    return [CachedProjectSummary(**row) for row in rows]


@router.post("/reopen-cache", response_model=UploadResponse)
async def reopen_cache(body: ReopenCacheRequest, container: ContainerDep) -> UploadResponse:
    """아티팩트 캐시에서 프로젝트 재오픈 — 새 doc_id 발급 후 즉시 복원."""
    try:
        document = await reopen_from_cache(container, body.content_hash)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return UploadResponse(doc_id=document.id, status="restored")


@router.post("/from-sample", response_model=UploadResponse)
async def create_from_sample(
    body: FromSampleRequest,
    background: BackgroundTasks,
    container: ContainerDep,
) -> UploadResponse:
    """data/raw/<name> 의 파일을 골라 처리 시작. 파일은 storage/incoming/로 복사한다."""
    raw_dir = container.settings.raw_data_dir
    src = resolve_sample_path(raw_dir, body.name)
    if src is None:
        raise HTTPException(404, f"샘플 없음: {body.name}")
    ext = src.suffix.lower()
    if ext not in EXT_TO_MIME:
        raise HTTPException(415, f"지원하지 않는 확장자: {ext}")

    tmp_id = uuid.uuid4().hex
    incoming_dir = container.settings.storage_root / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    dest = incoming_dir / f"{tmp_id}{ext}"
    shutil.copyfile(src, dest)

    service = ExtractionService(container)
    document = await service.prepare(dest)
    _order, labels, _featured = load_samples_manifest(container.settings.samples_manifest_path)
    display = sample_display_name(body.name, labels)
    document = document.model_copy(
        update={"source_filename": body.name, "display_name": display},
    )
    await container.repo.save_document(document)

    background.add_task(schedule_extraction_run, container, document)
    return UploadResponse(doc_id=document.id, status="queued")


@router.post("", response_model=UploadResponse)
async def upload_document(
    file: UploadFile,
    background: BackgroundTasks,
    container: ContainerDep,
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(400, "filename 누락")
    ext = Path(file.filename).suffix.lower()
    if ext not in EXT_TO_MIME:
        raise HTTPException(415, f"지원하지 않는 확장자: {ext}")

    tmp_id = uuid.uuid4().hex
    incoming_dir = container.settings.storage_root / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    dest = incoming_dir / f"{tmp_id}{ext}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    service = ExtractionService(container)
    document = await service.prepare(dest)
    original_name = Path(file.filename).name
    document = document.model_copy(
        update={
            "source_filename": original_name,
            "display_name": Path(original_name).stem,
        },
    )
    await container.repo.save_document(document)

    background.add_task(schedule_extraction_run, container, document)
    return UploadResponse(doc_id=document.id, status="queued")


class EnsurePipelineResponse(BaseModel):
    status: str
    reason: str | None = None


@router.post("/{doc_id}/ensure-pipeline", response_model=EnsurePipelineResponse)
async def ensure_pipeline(doc_id: str, container: ContainerDep) -> EnsurePipelineResponse:
    """리뷰 페이지 — 캐시 히트면 복원, 미스면 전체 파이프라인 재시작 (idempotent)."""
    result = await ensure_extraction_pipeline(container, doc_id)
    if result["status"] == "missing":
        raise HTTPException(404, result.get("reason", "document 없음"))
    return EnsurePipelineResponse(status=result["status"], reason=result.get("reason"))


class DocumentRenameRequest(BaseModel):
    display_name: str


class DocumentMetaResponse(BaseModel):
    doc_id: str
    title: str
    source_filename: str | None = None
    display_name: str | None = None
    content_hash: str | None = None


@router.get("/{doc_id}/meta", response_model=DocumentMetaResponse)
async def get_document_meta(doc_id: str, container: ContainerDep) -> DocumentMetaResponse:
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    doc = container.repo.documents[doc_id]
    return DocumentMetaResponse(
        doc_id=doc_id,
        title=resolve_document_title(doc),
        source_filename=doc.source_filename,
        display_name=doc.display_name,
        content_hash=doc.content_hash,
    )


@router.patch("/{doc_id}/meta", response_model=DocumentMetaResponse)
async def update_document_meta(
    doc_id: str,
    body: DocumentRenameRequest,
    container: ContainerDep,
) -> DocumentMetaResponse:
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    name = body.display_name.strip()
    if not name:
        raise HTTPException(400, "display_name 비어 있음")
    doc = container.repo.documents[doc_id]
    doc = doc.model_copy(update={"display_name": name[:120]})
    await container.repo.save_document(doc)
    return DocumentMetaResponse(
        doc_id=doc_id,
        title=resolve_document_title(doc),
        source_filename=doc.source_filename,
        display_name=doc.display_name,
        content_hash=doc.content_hash,
    )


@router.get("/{doc_id}/pipeline")
async def get_pipeline_status(doc_id: str, container: ContainerDep) -> dict[str, object]:
    """마지막 파이프라인 이벤트 — SSE 재연결·페이지 새로고침 시 상태 복원용."""
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    ev = container.event_bus.last_event(doc_id)
    base: dict[str, object] = {
        "llm_provider": container.settings.llm_provider,
        "llm_model": container.active_llm_model(),
    }
    tracker = container.llm_usage_by_doc.get(doc_id)
    if tracker:
        base["llm_usage"] = tracker.to_dict()
    if ev is None:
        return {
            "doc_id": doc_id,
            "stage": "UPLOADED",
            "payload": base,
            "history": [],
            "error": None,
            "timing_summary": {"total_elapsed_ms": 0, "from_cache": False},
        }
    history = container.event_bus.history(doc_id)
    total_ms = container.event_bus.total_elapsed_ms(doc_id)
    last_payload = ev.payload or {}
    from_cache = bool(last_payload.get("cached"))
    out = ev.model_dump(mode="json")
    out["history"] = history
    out["timing_summary"] = {
        "total_elapsed_ms": total_ms,
        "from_cache": from_cache,
        "recorded_total_ms": total_ms if from_cache else None,
    }
    out.update(base)
    return out


@router.get("/{doc_id}/llm-usage")
async def get_llm_usage(doc_id: str, container: ContainerDep) -> dict[str, object]:
    """문서별 LLM 호출·토큰·추정 비용."""
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    tracker = container.llm_usage_by_doc.get(doc_id)
    if tracker is None:
        return {
            "provider": container.settings.llm_provider,
            "model": container.active_llm_model(),
            "total_calls": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "total_cost_krw": 0,
            "recent_calls": [],
        }
    return tracker.to_dict()
