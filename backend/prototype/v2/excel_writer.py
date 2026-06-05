"""
스타일 적용 엑셀 출력 — 정답 조견표 구조 재현.

탭(시트)별 분리 + 3계위 컬럼 [ID | 항목명 | 요구사항 | 상세요건 | 출처].
항목명·요구사항은 정답처럼 연속 동일값을 셀 병합. 헤더 음영·틀고정,
데이터 테두리·줄바꿈·세로가운데, 자동 행높이, 자동 필터.
"""
from __future__ import annotations

import math
import re
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .extract import Req

# (헤더, Req 속성, 너비, 가운데정렬, 병합여부)
COLUMNS = [
    ("요구사항 ID", "rid", 16, True, False),
    ("항목명", "top", 22, False, True),
    ("요구사항", "mid", 26, False, True),
    ("상세요건", "detail", 82, False, False),
    ("출처", "source", 16, True, False),
]

_HEADER_FILL = PatternFill("solid", fgColor="305496")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
_ZEBRA_FILL = PatternFill("solid", fgColor="EEF3FB")
_AI_FILL = PatternFill("solid", fgColor="FCE4D6")   # AI 생성 셀(ID 등) 구분색(주황)
_BODY_FONT = Font(size=10)
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_WRAP = Alignment(wrap_text=True, vertical="center")
_WRAP_TOP = Alignment(wrap_text=True, vertical="top")
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

_DETAIL_W = next(w for _l, k, w, _c, _m in COLUMNS if k == "detail")
_KO_PER_LINE = max(10, int(_DETAIL_W / 2.0))
_INVALID_SHEET = re.compile(r"[\\/?*\[\]:]")


def _safe_sheet(name: str, used: set[str]) -> str:
    base = (_INVALID_SHEET.sub("_", name).strip() or "요구사항")[:28]
    name, i = base, 2
    while name in used:
        name = f"{base}_{i}"[:31]
        i += 1
    used.add(name)
    return name


def _row_height(detail: str) -> float | None:
    if not detail:
        return None
    lines = sum(max(1, math.ceil(len(seg) / _KO_PER_LINE)) for seg in detail.split("\n"))
    return min(15 + (lines - 1) * 14, 260) if lines > 1 else None


def _merge_runs(ws, col_idx: int, key_fn, reqs: list[Req]) -> None:
    """연속 동일 key 구간을 셀 병합."""
    start = 0
    n = len(reqs)
    while start < n:
        end = start
        while end + 1 < n and key_fn(reqs[end + 1]) == key_fn(reqs[start]) and key_fn(reqs[start]):
            end += 1
        if end > start:
            ws.merge_cells(start_row=start + 2, end_row=end + 2,
                           start_column=col_idx, end_column=col_idx)
            ws.cell(start + 2, col_idx).alignment = _WRAP_TOP
        start = end + 1


def _write_sheet(ws, reqs: list[Req]) -> None:
    for ci, (label, _k, width, _c, _m) in enumerate(COLUMNS, 1):
        cell = ws.cell(1, ci, label)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _CENTER
        cell.border = _BORDER
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 24

    for ri, r in enumerate(reqs, 2):
        vals = {"rid": r.rid, "top": r.top, "mid": r.mid, "detail": r.detail, "source": r.source}
        gen = {"rid": r.gen_rid, "top": r.gen_top, "mid": r.gen_mid}
        for ci, (_l, key, _w, center, _m) in enumerate(COLUMNS, 1):
            cell = ws.cell(ri, ci, vals[key])
            cell.border = _BORDER
            cell.font = _BODY_FONT
            cell.alignment = _CENTER if center else _WRAP
            if gen.get(key):          # AI 가 생성한 값만 주황 (원문 추출은 일반색)
                cell.fill = _AI_FILL
            elif ri % 2 == 1:
                cell.fill = _ZEBRA_FILL
        h = _row_height(r.detail)
        if h:
            ws.row_dimensions[ri].height = h

    # 항목명(top) / 요구사항(top+mid) 셀 병합
    _merge_runs(ws, 2, lambda r: r.top, reqs)
    _merge_runs(ws, 3, lambda r: (r.top, r.mid), reqs)

    ws.freeze_panes = "A2"
    if reqs:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{len(reqs) + 1}"


_TITLE_FONT = Font(bold=True, size=14, color="305496")
_SEC_FILL = PatternFill("solid", fgColor="D9E1F2")
_SEC_FONT = Font(bold=True, size=11, color="1F3864")


def _write_overview(ws, overview: dict) -> None:
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 78
    ws.column_dimensions["C"].width = 24
    ws.cell(1, 1, "RFP 개요").font = _TITLE_FONT
    row = 3

    def section(title: str):
        nonlocal row
        c = ws.cell(row, 1, title)
        c.fill = _SEC_FILL
        c.font = _SEC_FONT
        ws.cell(row, 2).fill = _SEC_FILL
        row += 1

    section("전체 요약")
    cell = ws.cell(row, 1, overview.get("summary", ""))
    cell.alignment = _WRAP
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
    h = _row_height(overview.get("summary", ""))
    if h:
        ws.row_dimensions[row].height = max(h, 60)
    row += 2

    section("핵심 기술")
    for ci, lbl in enumerate(("기술", "요구", "관련 요구사항 ID"), 1):
        c = ws.cell(row, ci, lbl)
        c.font = Font(bold=True, size=10)
        c.fill = _SEC_FILL
    row += 1
    for name, req, ids in overview.get("techs", []):
        ws.cell(row, 1, name).font = Font(bold=True, size=10)
        ws.cell(row, 1).alignment = _WRAP
        ws.cell(row, 2, req).alignment = _WRAP
        ws.cell(row, 3, ids).fill = _AI_FILL
        ws.cell(row, 3).alignment = _WRAP
        for ci in (1, 2, 3):
            ws.cell(row, ci).border = _BORDER
        row += 1
    row += 1

    section("핵심 RISK (독소조항)")
    for ci, lbl in enumerate(("관련 요구사항 ID", "리스크/독소조항"), 1):
        c = ws.cell(row, ci, lbl)
        c.font = Font(bold=True, size=10)
        c.fill = _SEC_FILL
    row += 1
    risks = overview.get("risks", [])
    if not risks:
        ws.cell(row, 1, "(식별된 독소조항 없음)").font = Font(italic=True, size=10, color="808080")
        row += 1
    for clause, ids in risks:
        ws.cell(row, 1, ids).alignment = _WRAP      # ID 먼저
        ws.cell(row, 1).fill = _AI_FILL
        ws.cell(row, 2, clause).alignment = _WRAP    # 내용 나중
        ws.cell(row, 1).border = _BORDER
        ws.cell(row, 2).border = _BORDER
        row += 1


def write_excel(reqs: list[Req], path, overview: dict | None = None) -> None:
    by_tab: "OrderedDict[str, list[Req]]" = OrderedDict()
    for r in reqs:
        by_tab.setdefault(r.tab or "요구사항", []).append(r)

    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    if overview:
        ov_ws = wb.create_sheet(title="개요")
        _write_overview(ov_ws, overview)
        used.add("개요")
    for tab, items in by_tab.items():
        ws = wb.create_sheet(title=_safe_sheet(tab, used))
        _write_sheet(ws, items)
    if not by_tab and not overview:
        wb.create_sheet(title="요구사항")
    wb.save(path)
