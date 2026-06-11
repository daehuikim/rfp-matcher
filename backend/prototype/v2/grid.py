"""
구조 모듈: 입력 문서 → dense grid (2D 텍스트 표).

opendataloader JSON 의 sparse cell(+rowspan/colspan)을 펼쳐 직사각 그리드로 복원한다.
도메인 지식 없음 — 순수하게 표 구조만 다룬다. 다른 변환기(hwpx 등)는 같은
Grid 추상을 내보내도록 별도 어댑터를 두면 동일 파이프라인을 재사용한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .text import norm, norm_lines


@dataclass
class Grid:
    cells: list[list[str]]        # [row][col] 텍스트
    table_id: int
    page: int | None
    next_id: int | None = None    # 연속표 연결(페이지 분할)
    row_pages: list[int | None] = field(default_factory=list)  # 행별 실제 페이지
    section_heading: str = ""     # HTML 직전 섹션 제목(탭·section_path 입력)

    def page_of(self, r: int) -> int | None:
        if 0 <= r < len(self.row_pages) and self.row_pages[r] is not None:
            return self.row_pages[r]
        return self.page

    @property
    def nrows(self) -> int:
        return len(self.cells)

    @property
    def ncols(self) -> int:
        return len(self.cells[0]) if self.cells else 0


def _cell_text(cell: dict) -> str:
    parts: list[str] = []

    def grab(o):
        if isinstance(o, dict):
            c = o.get("content")
            if isinstance(c, str):
                parts.append(c)
            for v in o.values():
                grab(v)
        elif isinstance(o, list):
            for v in o:
                grab(v)

    grab(cell.get("kids", []))
    return norm(" ".join(parts))


def _iter_tables(doc: dict):
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "table":
                yield o
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    yield from walk(doc)


def from_opendataloader_table(table: dict) -> Grid:
    nrows = table.get("number of rows", 0)
    ncols = table.get("number of columns", 0)
    cells = [["" for _ in range(ncols)] for _ in range(nrows)]
    row_pages: list[int | None] = [None] * nrows
    for row in table.get("rows", []):
        for c in row.get("cells", []):
            r0 = c["row number"] - 1
            c0 = c["column number"] - 1
            rs = c.get("row span", 1) or 1
            cs = c.get("column span", 1) or 1
            txt = _cell_text(c)
            pg = c.get("page number")
            for dr in range(rs):
                for dc in range(cs):
                    r, col = r0 + dr, c0 + dc
                    if 0 <= r < nrows and 0 <= col < ncols:
                        cells[r][col] = txt
                        if pg is not None and row_pages[r] is None:
                            row_pages[r] = pg
    return Grid(cells=cells, table_id=table.get("id", -1),
                page=table.get("page number"), next_id=table.get("next table id"),
                row_pages=row_pages)


def grids_from_opendataloader(doc: dict) -> list[Grid]:
    return [from_opendataloader_table(t) for t in _iter_tables(doc)]


# ----------------------------------------------------------------- HTML 어댑터

def read_html_bytes(raw: bytes) -> str:
    """인코딩 감지(utf-8 → cp949/euc-kr)."""
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _direct_rows(table) -> list:
    """top-level 표의 직계 `<tr>` 만 — 표안표 행 제외."""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _direct_cells(tr) -> list:
    return [c for c in tr.find_all(["td", "th"]) if c.find_parent("tr") is tr]


def _table_to_markdown(table) -> str:
    """중첩 표 → 파이프 구분 텍스트(상세요건 셀 내 표안표 보존)."""
    rows: list[list[str]] = []
    for tr in _direct_rows(table):
        cells = _direct_cells(tr)
        if not cells:
            continue
        row: list[str] = []
        for cell in cells:
            # 중첩 표는 재귀 변환(깊은 표안표)
            nested = cell.find("table")
            if nested is not None:
                inner = _table_to_markdown(nested)
                outer = norm(cell.get_text("\n", strip=True))
                if outer and inner:
                    row.append(f"{outer}\n{inner}")
                else:
                    row.append(inner or outer)
            else:
                row.append(norm(cell.get_text("\n", strip=True)))
        if any(c for c in row):
            rows.append(row)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    lines: list[str] = []
    for ri, row in enumerate(rows):
        padded = row + [""] * (width - len(row))
        lines.append(" | ".join(padded))
        if ri == 0 and width > 1:
            lines.append(" | ".join(["---"] * width))
    return "\n".join(lines)


def _reflow_inline_pipe_table(text: str) -> str:
    """한 줄로 붙은 표안표 마크다운 → 줄 단위 복원."""
    if "---" not in text or "|" not in text:
        return text
    m = re.search(
        r"([^\n|]+\|[^\n|]+)\s+---\s*\|\s*---\s*(.+)",
        text,
        re.DOTALL,
    )
    if not m:
        return text
    # 이미 줄 단위 pipe 표 — reflow 시 행 경계가 깨짐(법제처 SFR-004 등)
    if re.search(r"---\s*\|\s*---\s*\n", text):
        return text
    header = m.group(1).strip()
    body = m.group(2).strip()
    rows = [header, "--- | ---"]
    # 본문: '카테고리 | 내용' 행 — 다음 한글/영문 라벨+| 앞에서 분리
    for part in re.split(r"\s+(?=[\uac00-\ud7a3A-Za-z][\uac00-\ud7a3A-Za-z\s·]{0,14}\s*\|)", body):
        part = part.strip()
        if not part:
            continue
        if " | " in part:
            rows.append(part)
        elif rows:
            rows[-1] = f"{rows[-1]} {part}"
    rebuilt = "\n".join(rows)
    return text[: m.start()] + rebuilt + text[m.end() :]


def _html_cell_text(cell) -> str:
    """셀 텍스트 — 중첩 `<table>` 은 마크다운 표로 보존."""
    from bs4 import BeautifulSoup, NavigableString, Tag

    if not isinstance(cell, Tag):
        return norm(str(cell))
    nested = cell.find("table")
    if nested is None:
        return norm(cell.get_text("\n", strip=True))

    frag = BeautifulSoup(str(cell), "lxml")
    root = frag.find(["td", "th"]) or frag
    for table in list(root.find_all("table")):
        md = _table_to_markdown(table)
        table.replace_with(NavigableString(f"\n{md}\n" if md else ""))
    text = root.get_text("\n", strip=True)
    text = re.sub(r"\s*◦\s*", "\n◦ ", text)
    text = _reflow_inline_pipe_table(text)
    return norm_lines(text)


def _html_table_to_grid(table, idx: int) -> Grid:
    from bs4 import Tag

    trs = _direct_rows(table)
    # rowspan/colspan 펼치기: 셀을 배치하며 carry 칸 채움
    grid: list[list[str]] = []
    occupied: dict[tuple[int, int], bool] = {}
    for r, tr in enumerate(trs):
        if len(grid) <= r:
            grid.append([])
        c = 0
        for cell in _direct_cells(tr):
            while occupied.get((r, c)):
                c += 1
            rs = int(cell.get("rowspan", 1) or 1)
            cs = int(cell.get("colspan", 1) or 1)
            txt = _html_cell_text(cell)
            for dr in range(rs):
                for dc in range(cs):
                    rr, cc = r + dr, c + dc
                    while len(grid) <= rr:
                        grid.append([])
                    while len(grid[rr]) <= cc:
                        grid[rr].append("")
                    grid[rr][cc] = txt
                    occupied[(rr, cc)] = True
            c += cs
    width = max((len(row) for row in grid), default=0)
    for row in grid:
        row.extend([""] * (width - len(row)))
    return Grid(cells=grid, table_id=idx, page=None, section_heading="")


def _heading_from_context(soup, table) -> str:
    """표 직전 heading·단락 — LLM 스키마/section_path 힌트(탭 확정은 LLM 탭 배정)."""
    current = ""
    body = soup.body or soup
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "table"]):
        if el.name != "table" and el.find_parent("table"):
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            t = norm(el.get_text(" ", strip=True))
            if t:
                current = t
        elif el.name == "p":
            t = norm(el.get_text(" ", strip=True))
            # 짧은 단락만 맥락 힌트(본문 덩어리 제외) — 키워드 하드코딩 없음
            if 3 <= len(t) <= 120:
                current = t
        elif el is table:
            return current
    return current


def merge_consecutive_grids(grids: list[Grid]) -> list[Grid]:
    """페이지 분할로 쪼개진 연속 표 병합 — HTML/LibreOffice 경로용."""
    from .classify import classify, detect_header_row, is_requirement_table

    if len(grids) < 2:
        return grids

    out: list[Grid] = []
    cand: Grid | None = None
    cand_roles: dict[str, int] | None = None

    for grid in grids:
        header_row = detect_header_row(grid)
        if cand is not None and cand_roles is not None:
            continuation = header_row < 0 and cand.ncols == grid.ncols
            if continuation:
                skip = 0
                if grid.cells and header_row < 0:
                    # 반복 헤더 행 스킵
                    first = " ".join(grid.cells[0]).strip()
                    if cand.cells and first and first == " ".join(cand.cells[0]).strip():
                        skip = 1
                extra = grid.cells[skip:]
                cand.cells.extend(extra)
                cand.row_pages.extend(grid.row_pages[skip:] or [None] * len(extra))
                continue

        roles = classify(grid, header_row) if header_row >= 0 else {}
        if header_row >= 0 and is_requirement_table(grid, roles, header_row):
            if cand is not None:
                out.append(cand)
            cand = Grid(
                cells=[row[:] for row in grid.cells],
                table_id=grid.table_id,
                page=grid.page,
                row_pages=list(grid.row_pages),
                section_heading=grid.section_heading,
            )
            cand_roles = roles
            continue

        if cand is not None:
            out.append(cand)
            cand = cand_roles = None

    if cand is not None:
        out.append(cand)
    return out if out else grids


def grids_from_html(html: str) -> list[Grid]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    top = [t for t in soup.find_all("table") if t.find_parent("table") is None]
    raw: list[Grid] = []
    for i, t in enumerate(top):
        g = _html_table_to_grid(t, i)
        g.section_heading = _heading_from_context(soup, t)
        raw.append(g)
    return merge_consecutive_grids(raw)
