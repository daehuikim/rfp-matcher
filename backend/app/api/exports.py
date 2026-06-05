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

    # V2 LLM 개요(요약·핵심기술·리스크)가 있으면 개요 시트에 사용 (AI 칼럼은 앱 writer가 포함)
    v2_overview = _load_v2_overview(container, doc_id)
    RequirementSheetWriter().write(
        out_path,
        reqs,
        recommendations=recs,
        judgements=judges,
        mode=mode,
        columns=column_keys,
        adaptive=adaptive,
        layout=layout_key,
        v2_overview=v2_overview,
        extraction_meta=container.repo.documents.get(doc_id).extraction_meta
        if doc_id in container.repo.documents
        else None,
    )
    return FileResponse(
        path=out_path,
        filename=_download_name(filename, out_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _download_name(filename: str | None, out_path) -> str:
    """사용자 지정 다운로드 파일명 (없으면 out_path 이름). 경로 문자 제거."""
    if filename:
        safe = re.sub(r'[\\/:*?"<>|]+', "_", filename).strip() or out_path.stem
        return safe if safe.lower().endswith(".xlsx") else f"{safe}.xlsx"
    return out_path.name


def _load_v2_overview(container, doc_id: str):
    """V2 추출 시 생성한 LLM 개요(summary/techs/risks) dict — 없으면 None."""
    import pickle
    from pathlib import Path

    candidates = [container.settings.storage_root / doc_id / "v2_export.pkl"]
    doc = container.repo.documents.get(doc_id)
    if doc is not None and getattr(doc, "content_hash", None):
        candidates.append(
            container.settings.artifact_cache_dir / doc.content_hash[:16] / "v2_export.pkl"
        )
    pkl = next((p for p in candidates if Path(p).is_file()), None)
    if pkl is None:
        return None
    try:
        payload = pickle.loads(Path(pkl).read_bytes())
        return payload.get("overview")
    except Exception:  # noqa: BLE001
        return None
