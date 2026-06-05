"""
엑셀 출력 스타일 (단일 선언 소스).

조견표가 정답 엑셀처럼 보이도록 헤더 음영·볼드·틀고정, 데이터 테두리·줄바꿈·
세로 가운데, 컬럼 너비, 지브라 음영, 자동 행높이, 자동 필터를 적용한다.
컬럼 너비/색을 바꾸려면 이 파일의 선언부만 고친다.
"""
from __future__ import annotations

import math

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

HEADER_FILL = PatternFill("solid", fgColor="305496")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
ZEBRA_FILL = PatternFill("solid", fgColor="EEF3FB")
BODY_FONT = Font(size=10)
_THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
WRAP = Alignment(wrap_text=True, vertical="center")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

# export 컬럼 key -> (너비, 가운데정렬 여부)
COLUMN_STYLE: dict[str, tuple[int, bool]] = {
    "category": (18, False),
    "subcategory": (16, False),
    "code": (12, True),
    "name": (24, False),
    "definition": (50, False),
    "detail": (80, False),
    "source_page": (9, True),
    "source_section": (20, False),
    "deliverables": (40, False),
    "related": (18, False),
    "ai_risk": (10, True),
    "ai_reason": (50, False),
    "matched_solutions": (30, False),
    "missing_tech": (24, False),
    "consortium": (20, False),
    "human_mark": (10, True),
    "human_note": (30, False),
    "category_source": (12, True),
}
_DEFAULT = (18, False)
# 행높이를 좌우하는 긴 본문 컬럼
_TALL_KEYS = {"definition", "detail", "deliverables", "ai_reason", "matched_solutions", "human_note"}


def _row_height(ws: Worksheet, ri: int, col_keys: list[str]) -> float | None:
    lines = 1
    for ci, key in enumerate(col_keys, 1):
        if key not in _TALL_KEYS:
            continue
        val = ws.cell(ri, ci).value
        if not val:
            continue
        width, _ = COLUMN_STYLE.get(key, _DEFAULT)
        per_line = max(8, int(width / 2.0))  # 한글 ≈ 2배 폭
        n = 0
        for seg in str(val).split("\n"):
            n += max(1, math.ceil(len(seg) / per_line))
        lines = max(lines, n)
    if lines <= 1:
        return None
    return min(15 + (lines - 1) * 14, 260)


def style_data_sheet(ws: Worksheet, col_keys: list[str], n_rows: int) -> None:
    """헤더 1행 + 데이터 n_rows 가 이미 append 된 시트에 스타일 적용."""
    for ci, key in enumerate(col_keys, 1):
        cell = ws.cell(1, ci)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = BORDER
        width, _ = COLUMN_STYLE.get(key, _DEFAULT)
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 24

    for ri in range(2, n_rows + 2):
        for ci, key in enumerate(col_keys, 1):
            cell = ws.cell(ri, ci)
            cell.border = BORDER
            cell.font = BODY_FONT
            _, center = COLUMN_STYLE.get(key, _DEFAULT)
            cell.alignment = CENTER if center else WRAP
            if ri % 2 == 1:
                cell.fill = ZEBRA_FILL
        h = _row_height(ws, ri, col_keys)
        if h:
            ws.row_dimensions[ri].height = h

    ws.freeze_panes = "A2"
    if n_rows > 0:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(col_keys))}{n_rows + 1}"


def style_summary_sheet(ws: Worksheet) -> None:
    """총괄표: 너비·제목 강조만 가볍게."""
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 16
    if ws.max_row >= 1:
        ws.cell(1, 1).font = Font(bold=True, size=12)
