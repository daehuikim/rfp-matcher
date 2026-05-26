from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.phase1.loaders.base import EXT_TO_MIME
from app.services.extraction import ExtractionService

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


class FromSampleRequest(BaseModel):
    name: str


@router.get("/samples", response_model=list[SampleFile])
async def list_samples(container: ContainerDep) -> list[SampleFile]:
    """data/raw/에 비치된 샘플 RFP 파일 목록을 반환. 로컬 PoC용."""
    raw_dir = container.settings.raw_data_dir
    if not raw_dir.exists():
        return []
    out: list[SampleFile] = []
    for p in sorted(raw_dir.iterdir()):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext not in EXT_TO_MIME:
            continue
        out.append(
            SampleFile(
                name=p.name,
                size_bytes=p.stat().st_size,
                ext=ext.lstrip("."),
                display=p.stem.replace("_", " "),
            )
        )
    return out


@router.post("/from-sample", response_model=UploadResponse)
async def create_from_sample(
    body: FromSampleRequest,
    background: BackgroundTasks,
    container: ContainerDep,
) -> UploadResponse:
    """data/raw/<name> 의 파일을 골라 처리 시작. 파일은 storage/incoming/로 복사한다."""
    raw_dir = container.settings.raw_data_dir
    src = raw_dir / body.name
    # path traversal 차단
    if not src.is_file() or raw_dir.resolve() not in src.resolve().parents:
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

    async def _runner() -> None:
        try:
            await service.run(document)
        except Exception:
            logger.exception("extraction failed for %s", dest)

    background.add_task(_runner)
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

    async def _runner() -> None:
        try:
            await service.run(document)
        except Exception:
            logger.exception("extraction failed for %s", dest)

    background.add_task(_runner)
    return UploadResponse(doc_id=document.id, status="queued")


@router.get("/{doc_id}/pipeline")
async def get_pipeline_status(doc_id: str, container: ContainerDep) -> dict[str, object]:
    """마지막 파이프라인 이벤트 — SSE 재연결·페이지 새로고침 시 상태 복원용."""
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    ev = container.event_bus.last_event(doc_id)
    if ev is None:
        return {"doc_id": doc_id, "stage": "UPLOADED", "payload": {}, "error": None}
    return ev.model_dump(mode="json")
