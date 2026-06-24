from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.domain.models import ExtractionMetadata
from app.phase1.converters.libreoffice_paths import resolve_soffice_bin
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
    llm_provider: str | None = None


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
        if featured_set and _nfc(p.name) not in featured_set:
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
    return [by_name[n] for n in ordered_names if n in by_name]


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


class WorkspaceResetResponse(BaseModel):
    ok: bool = True
    storage_entries_removed: int = 0
    artifact_buckets_removed: int = 0


@router.post("/workspace/reset", response_model=WorkspaceResetResponse)
async def reset_workspace_endpoint(container: ContainerDep) -> WorkspaceResetResponse:
    """모든 프로젝트·캐시·storage 산출물 초기화 — 테스트·재시작용."""
    from app.services.workspace_reset import reset_workspace

    stats = await reset_workspace(container)
    return WorkspaceResetResponse(**stats)


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
    container.set_llm_provider(body.llm_provider)
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
    llm_provider: str | None = Form(default=None),
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(400, "filename 누락")
    container.set_llm_provider(llm_provider)
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


@router.post("/import-excel", response_model=UploadResponse)
async def import_excel(
    file: UploadFile,
    container: ContainerDep,
    llm_provider: str | None = Form(default=None),
) -> UploadResponse:
    """조견표 Excel(.xlsx) 업로드 → 역파싱해 프로젝트로 등록(편집본 재업로드/외부 조견표 불러오기).

    추출 칼럼 + AI/Human 판정을 복원한다. 추천이 비어 있으면(외부 조견표) 배경으로 AI 매칭 수행.
    """
    if not file.filename:
        raise HTTPException(400, "filename 누락")
    if Path(file.filename).suffix.lower() not in (".xlsx", ".xlsm"):
        raise HTTPException(415, "Excel(.xlsx) 파일만 업로드할 수 있습니다")
    container.set_llm_provider(llm_provider)

    from app.domain.enums import DocumentMime, PipelineStage
    from app.domain.models import Document
    from app.services.excel_import import parse_excel
    from app.services.pipeline import Pipeline

    doc_id = uuid.uuid4().hex
    incoming_dir = container.settings.storage_root / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    dest = incoming_dir / f"{doc_id}.xlsx"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        reqs, recs, juds = await asyncio.to_thread(parse_excel, dest, doc_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Excel 파싱 실패: {e}") from e
    if not reqs:
        raise HTTPException(422, "조견표 요구사항을 찾지 못했습니다(상세요건 칼럼 확인)")

    original = Path(file.filename).name
    document = Document(
        id=doc_id, src_path=dest, mime=DocumentMime.PDF,  # 컨테이너용 placeholder(요건 이미 존재→재추출 안 함)
        source_filename=original, display_name=Path(original).stem,
    )
    await container.repo.save_document(document)
    for req in reqs:
        await container.repo.append_requirement(doc_id, req)
    for rec in recs.values():
        await container.repo.upsert_recommendation(rec)
    for jud in juds.values():
        await container.repo.upsert_judgement(jud)

    pipeline = Pipeline(container.event_bus)
    await pipeline.emit(doc_id, PipelineStage.UPLOADED)
    await pipeline.emit(
        doc_id, PipelineStage.READY_FOR_REVIEW,
        payload={"requirements": len(reqs), "snippet": f"Excel 불러오기 · {len(reqs)}줄",
                 "imported": True},
    )
    if recs:
        await pipeline.emit(doc_id, PipelineStage.RECOMMENDED, payload={"recommendations": len(recs)})
    # 추천 없는 외부 조견표는 배경 AI 매칭
    if len(recs) < len(reqs):
        service = ExtractionService(container)
        asyncio.create_task(service._recommend_background(doc_id, use_cache=False))

    return UploadResponse(doc_id=doc_id, status="imported")


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
    mime: str | None = None
    has_source_file: bool = False  # 원본 파일을 /source 로 받을 수 있는지
    has_preview: bool = False  # /preview 로 우측 뷰어에 표시 가능한지 (PDF·변환·HTML)
    is_pdf: bool = False  # 원본이 PDF인지 (페이지 점프 가능 여부)
    preview_kind: str = "none"  # "pdf" | "html" | "none" — 위치 이동 방식 결정


def _source_path(doc) -> Path | None:
    """원본 파일이 디스크에 존재하면 경로 반환 (없으면 None)."""
    try:
        p = Path(doc.src_path)
    except (TypeError, ValueError):
        return None
    return p if p.is_file() else None


# ── 우측 뷰어용 미리보기 렌더링 ──
# 어떤 포맷이든 (PDF는 그대로, DOC/DOCX/HWP 등은 LibreOffice로 PDF 변환,
# 변환 실패 시 추출 단계의 converted.html) "브라우저에서 볼 수 있는" 형태로 제공.

_preview_locks: dict[str, asyncio.Lock] = {}


def _artifact_bucket(container, doc) -> Path | None:
    h = doc.content_hash
    if not h:
        return None
    return container.settings.artifact_cache_dir / h[:16]


def _converted_html_path(container, doc) -> Path | None:
    """추출 단계가 만든 converted.html (HWPX 등 PDF 변환 불가 시 폴백)."""
    bucket = _artifact_bucket(container, doc)
    if bucket:
        p = bucket / "converted.html"
        if p.is_file():
            return p
    return None


# 변환 HTML은 보통 CSS가 없어 브라우저 기본 명조체로 렌더된다.
# 가독성 좋은 한글 sans-serif + 표 테두리/여백을 주입한다.
_VIEWER_STYLE = """
<style id="rfp-viewer-style">
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css");
html, body {
  font-family: "Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont,
    "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif !important;
  font-size: 15px; line-height: 1.75; color: #1a1a1a; background: #fff;
  margin: 0; padding: 28px 32px; max-width: 1000px;
  -webkit-font-smoothing: antialiased;
}
* { font-family: inherit !important; letter-spacing: -0.01em; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px; }
td, th { border: 1px solid #d4d4d8; padding: 7px 10px; vertical-align: top; text-align: left; }
th { background: #f4f4f5; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
p { margin: 8px 0; }
img { max-width: 100%; height: auto; }
h1, h2, h3 { font-weight: 700; line-height: 1.4; margin: 20px 0 8px; }
</style>
"""


def _inject_viewer_style(html: str) -> str:
    """변환 HTML에 가독성 스타일 주입 (head 닫기 직전 / 없으면 body 시작 / 맨 앞)."""
    low = html.lower()
    idx = low.find("</head>")
    if idx != -1:
        return html[:idx] + _VIEWER_STYLE + html[idx:]
    idx = low.find("<body")
    if idx != -1:
        gt = html.find(">", idx)
        if gt != -1:
            return html[: gt + 1] + _VIEWER_STYLE + html[gt + 1 :]
    return _VIEWER_STYLE + html


async def _ensure_preview_pdf(container, doc) -> Path | None:
    """원본을 LibreOffice로 PDF 변환 (성공 시 캐시 경로 반환, 실패 시 None)."""
    if str(doc.mime) == "application/pdf":
        return _source_path(doc)

    src = _source_path(doc)
    if src is None:
        return None

    bucket = _artifact_bucket(container, doc)
    cache_pdf = (bucket / "preview.pdf") if bucket else None
    if cache_pdf and cache_pdf.is_file():
        return cache_pdf

    try:
        soffice = resolve_soffice_bin(getattr(container.settings, "soffice_bin", None))
    except FileNotFoundError:
        return None

    key = doc.content_hash or doc.id
    lock = _preview_locks.setdefault(key, asyncio.Lock())
    async with lock:
        if cache_pdf and cache_pdf.is_file():
            return cache_pdf
        out_dir = bucket if bucket else (container.settings.storage_root / "preview")
        out_dir.mkdir(parents=True, exist_ok=True)
        # 실행 중인 LibreOffice와 프로필 충돌 방지용 임시 UserInstallation
        profile = f"file://{(container.settings.storage_root / 'lo_profile').resolve()}"
        proc = await asyncio.create_subprocess_exec(
            soffice,
            "--headless",
            f"-env:UserInstallation={profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(src),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("preview PDF 변환 타임아웃 doc=%s", doc.id[:8])
            return None
        if proc.returncode != 0:
            logger.info(
                "preview PDF 변환 실패 doc=%s rc=%s: %s",
                doc.id[:8],
                proc.returncode,
                stderr.decode("utf-8", errors="replace")[:200],
            )
            return None
        produced = out_dir / f"{src.stem}.pdf"
        if not produced.is_file():
            return None
        if cache_pdf and produced != cache_pdf:
            produced.replace(cache_pdf)
            return cache_pdf
        return produced


def _has_preview(container, doc) -> bool:
    """미리보기를 만들 수 있는지 (변환 시도 없이 가볍게 판정)."""
    if _source_path(doc) is not None:
        return True
    return _converted_html_path(container, doc) is not None


def _preview_kind(container, doc) -> str:
    """뷰어가 받을 미리보기 형태 — 변환 시도 없이 결정.

    - "pdf"  : PDF 원본(페이지 점프) 또는 PDF 변환 예정
    - "html" : 추출 단계 converted.html (표 인덱스 앵커로 위치 이동 가능)
    - "none" : 미리보기 불가
    비-PDF는 위치 앵커(source_table_index)가 살아있는 HTML을 우선한다.
    """
    if str(doc.mime) == "application/pdf" and _source_path(doc) is not None:
        return "pdf"
    if _converted_html_path(container, doc) is not None:
        return "html"
    if _source_path(doc) is not None:
        return "pdf"  # LibreOffice 변환 예정
    return "none"


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
        mime=str(doc.mime) if doc.mime else None,
        has_source_file=_source_path(doc) is not None,
        has_preview=_has_preview(container, doc),
        is_pdf=str(doc.mime) == "application/pdf",
        preview_kind=_preview_kind(container, doc),
    )


@router.get("/{doc_id}/overview")
async def get_document_overview(doc_id: str, container: ContainerDep) -> dict:
    """V2/V3 추출 개요 — 웹 표시용. 없으면 빈 값."""
    from app.services.native_export import load_native_export

    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    doc = container.repo.documents[doc_id]
    payload = load_native_export(container.settings, doc_id, doc)
    if payload is None:
        return {"available": False}
    ov = payload.get("overview") or {}
    if not ov:
        return {"available": False}
    if ov.get("type") == "public_rfp":
        meta = ov.get("meta") or {}
        return {
            "available": True,
            "summary": meta.get("summary", ""),
            "techs": [list(t) for t in (meta.get("techs") or [])],
            "risks": [list(r) for r in (meta.get("risks") or [])],
            "summary_table": {
                "title": ov.get("sheet_title") or "요구사항 총괄표",
                "headers": ov.get("headers") or [],
                "rows": ov.get("rows") or [],
            },
        }
    return {
        "available": True,
        "summary": ov.get("summary", ""),
        "techs": [list(t) for t in (ov.get("techs") or [])],
        "risks": [list(r) for r in (ov.get("risks") or [])],
    }


@router.get("/{doc_id}/asset")
async def get_requirement_asset(
    doc_id: str,
    container: ContainerDep,
    path: str,
) -> FileResponse:
    """요건 상세에 첨부된 표·화면 PNG — prototype 추출 산출물."""
    from app.services.native_export import resolve_asset_path

    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    doc = container.repo.documents[doc_id]
    resolved = resolve_asset_path(container.settings, doc_id, doc, path)
    if resolved is None:
        raise HTTPException(404, "자산 없음")
    media = "image/png" if resolved.suffix.lower() == ".png" else "application/octet-stream"
    return FileResponse(
        resolved,
        media_type=media,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/{doc_id}/preview")
async def get_document_preview(doc_id: str, container: ContainerDep) -> Response:
    """우측 뷰어용 미리보기 — 어떤 포맷이든 볼 수 있는 형태로 제공.

    우선순위:
    ① PDF 원본 → 그대로 (source_page 로 페이지 점프)
    ② 비-PDF → 추출 단계 converted.html (source_table_index 앵커로 위치 이동)
    ③ HTML 없으면 → LibreOffice PDF 변환 (표시 전용)
    """
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    doc = container.repo.documents[doc_id]

    # ① PDF 원본
    if str(doc.mime) == "application/pdf":
        p = _source_path(doc)
        if p is not None:
            return FileResponse(
                p,
                media_type="application/pdf",
                content_disposition_type="inline",
                headers={"Cache-Control": "private, max-age=3600"},
            )

    # ② 비-PDF는 위치 앵커가 살아있는 converted.html 우선 (가독성 스타일 주입)
    html = _converted_html_path(container, doc)
    if html is not None:
        styled = _inject_viewer_style(html.read_text(encoding="utf-8", errors="replace"))
        return HTMLResponse(
            content=styled,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    # ③ HTML 없으면 LibreOffice PDF 변환 (표시 전용, 앵커 없음)
    pdf = await _ensure_preview_pdf(container, doc)
    if pdf is not None:
        return FileResponse(
            pdf,
            media_type="application/pdf",
            content_disposition_type="inline",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    raise HTTPException(404, "미리보기를 생성할 수 없습니다 (원본·변환 모두 불가)")


@router.get("/{doc_id}/source")
async def get_document_source(doc_id: str, container: ContainerDep) -> FileResponse:
    """원본 파일 바이트를 그대로 제공 — 우측 뷰어(페이지 확인)용. inline 표시.

    조견표의 source_page(1-based PDF 페이지)와 PDFium(브라우저 내장 뷰어)의
    `#page=N` 프래그먼트를 연동해 클릭 → 해당 페이지 이동을 지원한다.
    """
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    doc = container.repo.documents[doc_id]
    path = _source_path(doc)
    if path is None:
        raise HTTPException(404, "원본 파일 없음 (서버에 보관되지 않음)")
    media_type = str(doc.mime) if doc.mime else "application/octet-stream"
    filename = doc.source_filename or path.name
    return FileResponse(
        path,
        media_type=media_type,
        # 다운로드가 아니라 브라우저 내장 뷰어로 열리도록 inline.
        content_disposition_type="inline",
        filename=filename,
        headers={"Cache-Control": "private, max-age=3600"},
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
