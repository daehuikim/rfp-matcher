from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from app.domain.models import Requirement
from app.phase1.writers.sheet_writer import RequirementSheetWriter


def _req(rid: str, name: str, definition: str, detail: str) -> Requirement:
    return Requirement(
        id=rid,
        doc_id="d",
        category="탭",
        code=rid,
        name=name,
        definition=definition,
        detail=detail,
    )


def test_export_merges_name_and_definition_columns(tmp_path: Path) -> None:
    reqs = [
        _req("r1", "항목A", "요구1", "상세1"),
        _req("r2", "항목A", "요구1", "상세2"),
        _req("r3", "항목A", "요구2", "상세3"),
    ]
    out = RequirementSheetWriter().write(
        tmp_path / "m.xlsx",
        reqs,
        columns=["name", "definition", "detail"],
        adaptive=False,
    )
    wb = load_workbook(out)
    ws = wb["탭"]
    merges = [str(m) for m in ws.merged_cells.ranges]
    assert any(m.startswith("A2:A") for m in merges)  # name (항목A 연속)
    assert "B2:B3" in merges  # definition (요구1 연속)
