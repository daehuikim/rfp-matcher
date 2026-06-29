"""스펙 5단계 — 조견표 db → Excel. Section=탭, [항목명·요구사항·상세요건] (+참조·출처)."""
from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .db import Row

_HDR = Font(bold=True, size=10, color="FFFFFF")
_HDR_FILL = PatternFill("solid", fgColor="404040")
_WRAP = Alignment(wrap_text=True, vertical="top")
_BORDER = Border(*[Side(style="thin", color="D0D0D0")] * 4)
_COLS = ["항목명", "요구사항", "상세요건", "참조사항", "출처"]


def _safe(title: str, used: set[str]) -> str:
    t = re.sub(r"[\\/*?:\[\]]", " ", title or "요구사항").strip()[:31] or "요구사항"
    base, i = t, 2
    while t in used:
        t = f"{base[:28]}_{i}"; i += 1
    used.add(t)
    return t


def write_excel(rows: list[Row], out_path: str | Path) -> dict:
    """rows → Excel. 반환: 통계(시트수·행수)."""
    by_section: "OrderedDict[str, list[Row]]" = OrderedDict()
    for r in rows:
        by_section.setdefault(r.section or "요구사항", []).append(r)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for section, srows in by_section.items():
        ws = wb.create_sheet(_safe(section, used))
        for ci, h in enumerate(_COLS, 1):
            c = ws.cell(1, ci, h); c.font = _HDR; c.fill = _HDR_FILL
        widths = [22, 26, 70, 14, 12]
        for ci, w in enumerate(widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
        prev_item = prev_req = None
        for ri, r in enumerate(srows, 2):
            # 셀 병합 형태(연속 동일 항목명/요구사항은 빈칸)
            item = "" if r.item == prev_item and r.item else r.item
            req = "" if (r.requirement == prev_req and r.requirement and item == "") else r.requirement
            src = f"p.{r.page}" if r.page else ""
            vals = [item, req, r.detail, r.ref, src]
            for ci, v in enumerate(vals, 1):
                cell = ws.cell(ri, ci, v); cell.alignment = _WRAP; cell.border = _BORDER
            prev_item, prev_req = r.item, r.requirement
    if not by_section:
        wb.create_sheet("요구사항")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return {"sheets": len(by_section), "rows": len(rows)}
