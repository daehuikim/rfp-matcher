from __future__ import annotations
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.domain.enums import ExportMode
from app.phase1.writers.export_columns import (
    EXPORT_COLUMNS,
    EXPORT_PRESETS,
    list_applicable_columns,
    resolve_export_columns,
)
from app.phase1.writers.sheet_writer import RequirementSheetWriter

router = APIRouter(prefix="/documents", tags=["exports"])


class ExportColumnInfo(BaseModel):
    key: str
    header: str
    group: str


class ExportColumnsResponse(BaseModel):
    preset: str
    mode: str
    selected: list[str]
    applicable: list[ExportColumnInfo]
    presets: dict[str, list[str]]


@router.get("/{doc_id}/export/columns", response_model=ExportColumnsResponse)
async def list_export_columns(
    doc_id: str,
    container: ContainerDep,
    mode: ExportMode = ExportMode.BOTH,
    preset: str = "standard",
) -> ExportColumnsResponse:
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    reqs, recs, judges = await container.repo.snapshot(doc_id)
    preset_key = preset if preset in EXPORT_PRESETS else "standard"
    selected = resolve_export_columns(reqs, recs, judges, mode, EXPORT_PRESETS[preset_key])
    applicable_keys = list_applicable_columns(reqs, recs, judges, mode)
    return ExportColumnsResponse(
        preset=preset_key,
        mode=mode.value,
        selected=selected,
        applicable=[
            ExportColumnInfo(key=k, header=EXPORT_COLUMNS[k].header, group=EXPORT_COLUMNS[k].group)
            for k in applicable_keys
        ],
        presets=EXPORT_PRESETS,
    )


@router.get("/{doc_id}/export")
async def export_excel(
    doc_id: str,
    container: ContainerDep,
    mode: ExportMode = ExportMode.BOTH,
    cols: str | None = None,
    adaptive: bool = True,
    layout: str = "cluster",
    filename: str | None = None,
) -> FileResponse:
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    reqs, recs, judges = await container.repo.snapshot(doc_id)
    if not reqs:
        raise HTTPException(409, "추출된 요구사항 없음")

    layout_key = "ordered" if layout == "ordered" else "cluster"
    column_keys = [c.strip() for c in cols.split(",")] if cols else None

    out_dir = container.settings.storage_root / doc_id / "exports"
    col_tag = cols.replace(",", "-")[:40] if cols else "adaptive"
    out_path = out_dir / f"requirements_{mode.value}_{layout_key}_{col_tag}.xlsx"
    RequirementSheetWriter().write(
        out_path,
        reqs,
        recommendations=recs,
        judgements=judges,
        mode=mode,
        columns=column_keys,
        adaptive=adaptive,
        layout=layout_key,
        extraction_meta=container.repo.documents.get(doc_id).extraction_meta
        if doc_id in container.repo.documents
        else None,
    )
    # 사용자 지정 다운로드 파일명 (없으면 기본 생성). 경로 문자 제거.
    if filename:
        safe = re.sub(r'[\\/:*?"<>|]+', "_", filename).strip() or out_path.stem
        download_name = safe if safe.lower().endswith(".xlsx") else f"{safe}.xlsx"
    else:
        download_name = out_path.name
    return FileResponse(
        path=out_path,
        filename=download_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
