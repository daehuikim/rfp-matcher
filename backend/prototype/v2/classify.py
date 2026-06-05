"""
스키마 드라이븐 컬럼 분류기 (제너릭 스코어러).

입력: Grid + RoleSpec 스키마.
동작: 모든 (컬럼 x 역할) 점수 행렬을 계산 → 점수 높은 순으로 1:1 배정.
역할별 if/elif·우선순위 하드코딩·fallback 없음. 행동은 schema.py 만 바꿔 조정.

점수 = 어휘점수(헤더가 역할 어휘를 포함) + 형상점수(데이터 통계가 역할 신호와 부합).
"""
from __future__ import annotations

from dataclasses import dataclass

from .grid import Grid
from .schema import REQUIRED_ROLES, REQUIREMENT_SCHEMA, RoleSpec
from .text import looks_like_code, norm, sig


@dataclass
class ColumnStats:
    nonempty_ratio: float
    avg_len: float
    code_ratio: float
    repeat_ratio: float    # 인접 행 동일값 비율(병합 carry 신호)


def detect_header_row(grid: Grid) -> int:
    """row0 가 헤더인지 스키마 어휘로 판정. 헤더면 0, 없으면 -1(전 행 데이터).

    연속표(페이지 분할)는 헤더가 없으므로 -1 이 되어 첫 행이 데이터로 보존된다.
    """
    if grid.nrows == 0:
        return -1
    row0 = grid.cells[0]
    hits = 0
    for cell in row0:
        hs = sig(cell).lower()
        raw = norm(cell).lower()
        if not hs:
            continue
        for spec in REQUIREMENT_SCHEMA:
            if any(sig(t).lower() in hs or t in raw for t in spec.header_terms):
                hits += 1
                break
    if hits >= 2:
        return 0

    # 형상 기반: row0 가 모두 짧은 라벨이고, 그 아래 데이터가 훨씬 긴 컬럼이면 헤더
    # (역할 어휘에 없는 라벨 '주요 역할/제안 기준' 등도 잡음; 연속표는 row0 에 긴 셀이 있어 제외됨)
    if grid.nrows >= 3:
        nonempty = [c for c in row0 if c]
        if len(nonempty) >= 2 and all(len(c) <= 12 for c in nonempty):
            longer = 0
            for ci in range(grid.ncols):
                if not row0[ci]:
                    continue
                lens = [len(grid.cells[r][ci]) for r in range(1, grid.nrows) if grid.cells[r][ci]]
                avg = sum(lens) / len(lens) if lens else 0
                if avg >= max(15, len(row0[ci]) * 2):
                    longer += 1
            if longer >= max(1, len(nonempty) - 1):
                return 0
    return -1


def _column_stats(grid: Grid, col: int, header_row: int) -> ColumnStats:
    vals = [grid.cells[r][col] for r in range(header_row + 1, grid.nrows)]
    nonempty = [v for v in vals if v]
    n = len(vals) or 1
    avg_len = sum(len(v) for v in nonempty) / (len(nonempty) or 1)
    code = sum(1 for v in nonempty if looks_like_code(v)) / (len(nonempty) or 1)
    repeats = sum(1 for a, b in zip(vals, vals[1:]) if a and a == b) / (len(vals) - 1 or 1)
    return ColumnStats(
        nonempty_ratio=len(nonempty) / n,
        avg_len=avg_len,
        code_ratio=code,
        repeat_ratio=repeats,
    )


def _header_score(header: str, spec: RoleSpec) -> float:
    hs = sig(header).lower()
    raw = norm(header).lower()
    if not hs:
        return 0.0
    best = 0.0
    for term in spec.header_terms:
        ts = sig(term).lower()
        if ts and (ts in hs or term in raw):
            # 더 긴(구체적) 용어 매칭일수록 높은 점수
            best = max(best, 0.5 + 0.5 * min(1.0, len(ts) / 6))
    return best


def _shape_score(st: ColumnStats, spec: RoleSpec) -> float:
    """형상 신호와 역할 선호의 부합도 [0..1 범위 합산]."""
    s = 0.0
    if spec.long_text:
        # 평균 길이 20자→0.5, 40자 이상→1.0 로 포화
        s += min(1.0, st.avg_len / 40.0)
    if spec.short_code:
        s += st.code_ratio
    if spec.repeats:
        s += st.repeat_ratio
    # 형상 선호가 전혀 없는 역할(item/note)은 형상 중립
    return s


def _score(header: str, st: ColumnStats, spec: RoleSpec) -> float:
    return 1.0 * _header_score(header, spec) + 0.7 * _shape_score(st, spec)


def classify(grid: Grid, header_row: int = 0,
             schema: tuple[RoleSpec, ...] = REQUIREMENT_SCHEMA) -> dict[str, int]:
    """역할 이름 -> 컬럼 인덱스. (배정 안 된 역할은 키 없음)"""
    if grid.nrows <= header_row + 1 or grid.ncols == 0:
        return {}
    header = grid.cells[header_row] if header_row >= 0 else [""] * grid.ncols
    stats = [_column_stats(grid, c, header_row) for c in range(grid.ncols)]

    # 점수 행렬
    triples: list[tuple[float, str, int]] = []
    for spec in schema:
        for col in range(grid.ncols):
            sc = _score(header[col] if col < len(header) else "", stats[col], spec)
            if sc > 0:
                triples.append((sc, spec.name, col))
    triples.sort(reverse=True)

    assigned_role: dict[str, int] = {}
    used_cols: set[int] = set()
    for sc, rname, col in triples:
        if rname in assigned_role or col in used_cols:
            continue
        assigned_role[rname] = col
        used_cols.add(col)
    return assigned_role


def column_stats(grid: Grid, col: int, header_row: int = 0) -> ColumnStats:
    return _column_stats(grid, col, header_row)


def is_requirement_table(grid: Grid, roles: dict[str, int], header_row: int = 0) -> bool:
    """스키마 기반 판정: 필수 역할(detail)이 채워졌고 그 컬럼이 실제 본문성."""
    for req in REQUIRED_ROLES:
        if req not in roles:
            return False
    det = roles["detail"]
    st = _column_stats(grid, det, header_row)
    # detail 컬럼이 충분히 채워져 있고 평균적으로 본문 길이여야 함
    return st.nonempty_ratio >= 0.3 and st.avg_len >= 12
