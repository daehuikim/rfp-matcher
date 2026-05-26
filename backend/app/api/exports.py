from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import ContainerDep
from app.domain.enums import ExportMode
from app.phase1.writers.sheet_writer import RequirementSheetWriter

router = APIRouter(prefix="/documents", tags=["exports"])


@router.get("/{doc_id}/export")
async def export_excel(
    doc_id: str,
    container: ContainerDep,
    mode: ExportMode = ExportMode.BOTH,
    cols: str | None = None,
) -> FileResponse:
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    reqs, recs, judges = await container.repo.snapshot(doc_id)
    if not reqs:
        raise HTTPException(409, "추출된 요구사항 없음")

    column_keys = [c.strip() for c in cols.split(",")] if cols else None

    out_dir = container.settings.storage_root / doc_id / "exports"
    col_tag = cols.replace(",", "-")[:40] if cols else "default"
    out_path = out_dir / f"requirements_{mode.value}_{col_tag}.xlsx"
    RequirementSheetWriter().write(
        out_path,
        reqs,
        recommendations=recs,
        judgements=judges,
        mode=mode,
        columns=column_keys,
    )
    return FileResponse(
        path=out_path,
        filename=out_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
