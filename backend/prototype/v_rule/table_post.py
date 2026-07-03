"""스펙 2단계 — HTML 표 후처리.

(1) 여러 페이지 표 조각 → 하나로 합침
(2) 밑칸 비면 윗칸 병합(상위 칼럼 rowspan 확장) = forward-fill(상세열 제외)
(3) 윗-아래칸 조각문장 이어붙이기
(4) 예외: 상단밑줄+헤더 결합으로 생긴 양끝 빈칼럼 제거
(5) 예외: 헤더표/본문표 분리(헤더표 윗줄 없어 본문 인식) → 결합 복원
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag


@dataclass
class Table:
    rows: list[list[str]]              # 직사각 그리드
    section_hint: str = ""             # 표 직전 heading/단락(컨텍스트)
    header_row: int = -1              # 헤더 행 인덱스(없으면 -1)
    page: int | None = None

    @property
    def ncols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    @property
    def nrows(self) -> int:
        return len(self.rows)


def _cell_text(cell: Tag) -> str:
    # 중첩표는 텍스트로 평탄화(스펙은 표 단위 처리 — 중첩은 드묾)
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()


def _expand_spans(table: Tag) -> list[list[str]]:
    """rowspan/colspan 펼침 → 직사각 그리드."""
    grid: list[list[str]] = []
    occupied: dict[tuple[int, int], bool] = {}
    trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    for r, tr in enumerate(trs):
        while len(grid) <= r:
            grid.append([])
        c = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while occupied.get((r, c)):
                c += 1
            rs = int(cell.get("rowspan", 1) or 1)
            cs = int(cell.get("colspan", 1) or 1)
            txt = _cell_text(cell)
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
    return grid


# ── 헤더 감지(룰) — 첫 행이 헤더 어휘/짧은 라벨이면 헤더 ──────────────────────
_HEADER_TERMS = ("구분", "항목", "요구사항", "상세", "내용", "기능", "규격", "비고",
                 "번호", "no", "수량", "단위", "평가", "배점", "분야", "세부")


def _detect_header(grid: list[list[str]]) -> int:
    if not grid:
        return -1
    row0 = [c.strip() for c in grid[0]]
    nonempty = [c for c in row0 if c]
    if not nonempty:
        return -1
    short = sum(1 for c in nonempty if len(c) <= 12)
    term = sum(1 for c in nonempty if any(t in c.lower() for t in _HEADER_TERMS))
    if term >= 1 and short >= max(1, len(nonempty) // 2):
        return 0
    return -1


def _heading_before(soup: BeautifulSoup, table: Tag) -> str:
    cur = ""
    body = soup.body or soup
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "table"]):
        if el.name != "table" and el.find_parent("table"):
            continue
        if el is table:
            return cur
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
            if t:
                cur = t
        elif el.name == "p":
            t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
            if 2 <= len(t) <= 80:
                cur = t
    return cur


# ── (4) 양끝 빈칼럼 제거 ────────────────────────────────────────────────────
def _trim_edge_empty_cols(grid: list[list[str]]) -> list[list[str]]:
    if not grid:
        return grid
    n = max(len(r) for r in grid)
    keep = [c for c in range(n) if any((len(r) > c and r[c].strip()) for r in grid)]
    # 양끝(맨앞/맨뒤)만 제거 — 중간 빈칼럼은 보존(병합 흔적일 수 있음)
    if not keep:
        return grid
    lo, hi = keep[0], keep[-1]
    return [[(r[c] if c < len(r) else "") for c in range(lo, hi + 1)] for r in grid]


# ── (3) 조각문장 이어붙이기 ─────────────────────────────────────────────────
_BULLET_HEAD = re.compile(r"^\s*(?:[-*∙•·–—○◦●❍▪◇■□]|[가-힣]\.|\d+[.)]|[①-⑳]|[IVX]+\.)\s")
_SENT_END = re.compile(r"(?:다|음|함|됨|오|요|임|것|이다|한다|된다)[.]?\s*$|[.。:;]\s*$")


def _join_wrapped(grid: list[list[str]], header_row: int) -> list[list[str]]:
    """한 셀 문장이 다음 행으로 줄바꿈된 조각 → 이전 행 같은 칼럼에 이어붙임.

    연속행 판정: 다른 칼럼은 비고 마지막(상세) 칼럼만 차 있으며, 그 텍스트가 새 불릿으로
    시작하지 않고, 직전 행 상세가 문장종결로 안 끝났을 때.
    """
    if not grid:
        return grid
    n = max(len(r) for r in grid)
    out: list[list[str]] = []
    start = 0
    if header_row == 0 and grid:
        out.append(grid[0]); start = 1
    for r in range(start, len(grid)):
        row = grid[r] + [""] * (n - len(grid[r]))
        nonempty_cols = [c for c in range(n) if row[c].strip()]
        last = n - 1
        is_cont = (
            out and nonempty_cols == [last] and row[last].strip()
            and not _BULLET_HEAD.match(row[last])
            and len(out[-1]) > last and out[-1][last].strip()
            and not _SENT_END.search(out[-1][last])
        )
        if is_cont:
            out[-1][last] = (out[-1][last].rstrip() + " " + row[last].strip()).strip()
        else:
            out.append(row)
    return out


# ── (2) 밑칸 비면 윗칸 병합(forward-fill, 상세열 제외) ──────────────────────
def _fill_down(grid: list[list[str]], header_row: int) -> list[list[str]]:
    if not grid:
        return grid
    n = max(len(r) for r in grid)
    last = n - 1                      # 상세열은 fill 안 함(atomic 보존)
    carry = [""] * n
    out = []
    for r, row in enumerate(grid):
        row = list(row) + [""] * (n - len(row))
        if r == header_row:
            out.append(row); continue
        for c in range(last):        # 상위(카테고리) 칼럼만 forward-fill
            if row[c].strip():
                carry[c] = row[c]
                for j in range(c + 1, last):
                    carry[j] = ""    # 상위 갱신되면 하위 carry 리셋
            else:
                row[c] = carry[c]
        out.append(row)
    return out


# ── (1) 다페이지 표 조각 병합 + (5) 헤더/본문 결합 복원 ──────────────────────
def merge_fragments(tables: list[Table]) -> list[Table]:
    """연속 표 조각 병합: 헤더없는 연속(동일 ncols) → 직전에 흡수.
    (5) 헤더표(헤더만, 1~2행) 바로 뒤 본문표(동일 ncols) → 결합.
    """
    if not tables:
        return tables
    out: list[Table] = []
    for t in tables:
        if out:
            prev = out[-1]
            same = t.ncols == prev.ncols and t.ncols > 1
            # (5) 직전이 헤더만 있는 표(1행) + 현재 본문(헤더없음) → 결합
            prev_header_only = prev.nrows <= 1 and prev.header_row == 0
            # (1) 현재가 헤더없는 연속 조각
            cont = t.header_row < 0
            if same and (cont or prev_header_only):
                # 반복 헤더 행 스킵
                body = t.rows
                if (prev.rows and t.rows and t.header_row == 0
                        and " ".join(t.rows[0]) == " ".join(prev.rows[0])):
                    body = t.rows[1:]
                prev.rows.extend(body)
                continue
        out.append(t)
    return out


def tables_from_html(html: str) -> list[Table]:
    """HTML → 후처리 완료된 Table 리스트(스펙 2단계 전부 적용)."""
    soup = BeautifulSoup(html, "lxml")
    raw: list[Table] = []
    for tbl in soup.find_all("table"):
        if tbl.find_parent("table") is not None:
            continue                  # top-level 만
        grid = _expand_spans(tbl)
        if not any(c.strip() for row in grid for c in row):
            continue
        hr = _detect_header(grid)
        raw.append(Table(rows=grid, section_hint=_heading_before(soup, tbl), header_row=hr))
    # (1)+(5) 병합
    merged = merge_fragments(raw)
    # (3)(4)(2) 셀 수준 후처리
    for t in merged:
        t.rows = _trim_edge_empty_cols(t.rows)         # (4)
        t.header_row = _detect_header(t.rows)
        t.rows = _join_wrapped(t.rows, t.header_row)    # (3)
        t.rows = _fill_down(t.rows, t.header_row)       # (2)
    return merged
