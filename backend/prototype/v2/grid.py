"""
구조 모듈: 입력 문서 → dense grid (2D 텍스트 표).

opendataloader JSON 의 sparse cell(+rowspan/colspan)을 펼쳐 직사각 그리드로 복원한다.
도메인 지식 없음 — 순수하게 표 구조만 다룬다. 다른 변환기(hwpx 등)는 같은
Grid 추상을 내보내도록 별도 어댑터를 두면 동일 파이프라인을 재사용한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .text import norm


@dataclass
class Grid:
    cells: list[list[str]]        # [row][col] 텍스트
    table_id: int
    page: int | None
    next_id: int | None = None    # 연속표 연결(페이지 분할)
    row_pages: list[int | None] = field(default_factory=list)  # 행별 실제 페이지

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


def _html_table_to_grid(table, idx: int) -> Grid:
    from bs4 import Tag

    trs = table.find_all("tr")
    # rowspan/colspan 펼치기: 셀을 배치하며 carry 칸 채움
    grid: list[list[str]] = []
    occupied: dict[tuple[int, int], bool] = {}
    for r, tr in enumerate(trs):
        if len(grid) <= r:
            grid.append([])
        c = 0
        for cell in tr.find_all(["td", "th"]):
            while occupied.get((r, c)):
                c += 1
            rs = int(cell.get("rowspan", 1) or 1)
            cs = int(cell.get("colspan", 1) or 1)
            txt = norm(cell.get_text("\n", strip=True))
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
    return Grid(cells=grid, table_id=idx, page=None)


def grids_from_html(html: str) -> list[Grid]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    # 최상위 표만 — 셀 안에 중첩된 하위표는 부모 표에 이미 포함되므로 중복 추출 방지
    top = [t for t in soup.find_all("table") if t.find_parent("table") is None]
    return [_html_table_to_grid(t, i) for i, t in enumerate(top)]
