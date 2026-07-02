"""표(grid) → 구조 인식 요구사항 추출. 도메인 키워드 하드코딩 없이 **구조 신호**만 사용.

문제(진단): 요구사항이 다열표 안에 있는데 셀을 통째로 join하면 계위/요구사항명이 소실되고
배점표·현황표·다이어그램 격자가 행으로 누출된다. 여기서 표를 3종으로 분류해 처리한다:
  - junk       : 도표/현황/배점(열 많음·숫자 비중 높음·라벨만) → 드롭
  - vertical   : 세로 라벨-값 카드(HWP: [고유번호|ECR-001][요구사항 명칭|..][세부 내용|❍..]) → 1표=1요구
  - horizontal : 가로 요구표([구분|내용|비고], [No|항목명|요구사항|상세요건]) → 데이터행마다 요구
                 내용열=상세, 구분/항목열=계위(rowspan forward-fill), 헤더행 제거

반환: list[dict{level,name,detail}]  (junk면 []).  텍스트는 셀 원문 그대로(충실 전사).
"""
from __future__ import annotations

import re

# 숫자/기호만(현황·배점 셀) — 단위·연도 등 포함
_NUM_ONLY = re.compile(r"^[\s\d.,%~/()\-–—:·+*°]+(?:[가-힣A-Za-z]{0,3})?$")
_ID_PAT = re.compile(r"[A-Z]{2,5}[-–]?\d{2,4}")            # ECR-001, SFR001 등 요구사항 ID
# 한 셀/문자열 안 복수 항목 구분자(불릿·개행-대시·<br>)
_ITEM_SPLIT = re.compile(r"\s*(?:[❍○●◦▪▫◆◇▶▷▸‣⁃∙•⦁]|<br\s*/?>)\s*")


def _norm(c: str) -> str:
    return re.sub(r"\s+", " ", (c or "").strip())


def split_items(text: str) -> list[str]:
    """한 문자열 안에 불릿(❍/•/⦁)·<br>로 나열된 복수 항목을 개별로 분해. 하나면 그대로 1개."""
    parts = [p.strip(" ·-–—\t") for p in _ITEM_SPLIT.split(text or "")]
    parts = [p for p in parts if p and len(p) >= 3]
    return parts or ([text.strip()] if text and text.strip() else [])


def _col_stats(grid: list[list[str]]) -> tuple[int, list[dict]]:
    ncol = max((len(r) for r in grid), default=0)
    stats = []
    for c in range(ncol):
        cells = [_norm(r[c]) for r in grid if c < len(r) and _norm(r[c])]
        if not cells:
            stats.append({"avglen": 0.0, "numratio": 1.0, "n": 0, "maxlen": 0})
            continue
        stats.append({
            "avglen": sum(len(x) for x in cells) / len(cells),
            "numratio": sum(1 for x in cells if _NUM_ONLY.match(x)) / len(cells),
            "n": len(cells),
            "maxlen": max(len(x) for x in cells),
        })
    return ncol, stats


def is_junk_table(grid: list[list[str]]) -> bool:
    """도표/현황/배점 격자 판정 — 열이 많거나 숫자 비중이 높거나 서술 셀이 거의 없음."""
    cells = [_norm(c) for r in grid for c in r if _norm(c)]
    if len(cells) < 2:
        return True
    ncol, stats = _col_stats(grid)
    numratio = sum(1 for x in cells if _NUM_ONLY.match(x)) / len(cells)
    if numratio > 0.45:                       # 배점표/현황 숫자 격자
        return True
    if ncol >= 6:                             # 다이어그램/광폭 격자
        return True
    # 서술 셀(15자↑)이 전혀 없으면 요구 본문 없음(라벨/도표)
    if max((s["maxlen"] for s in stats), default=0) < 15:
        return True
    # (집계/수량표 규칙 제거: 순번(No) 열 있는 진짜 요구표까지 드롭돼 recall 손실.
    #  총괄표/집계표는 gemma keep 이 제목 기반으로 처리)
    return False


def _is_vertical_card(grid: list[list[str]], ncol: int, stats: list[dict]) -> bool:
    """세로 라벨-값 카드(HWP 요구사항 정의표): 좌열이 짧은 라벨(고유번호/명칭/세부내용…),
    우측에 값. 일부 행이 3열(라벨|소라벨|값)이어도 허용."""
    if ncol not in (2, 3):
        return False
    rows2 = [r for r in grid if len(r) >= 2 and _norm(r[0])]
    if len(rows2) < 3:
        return False
    left = [_norm(r[0]) for r in rows2]
    short = sum(1 for x in left if len(x) <= 18) / len(left)
    has_long = any(len(_norm(c)) >= 15 for r in grid for c in r[1:])   # 세부내용 같은 서술값
    return short > 0.7 and has_long


def _vertical_to_reqs(grid: list[list[str]]) -> list[dict]:
    """세로 라벨-값 카드 → 1요구. name=제목값(요구사항 명칭), code=ID값(고유번호),
    detail=최장값(세부 내용, ❍ 분해). 값이 없는 라벨행(관련 요구사항/산출정보 빈칸)은 무시."""
    pairs = [(_norm(r[0]), " ".join(_norm(c) for c in r[1:] if _norm(c))) for r in grid if len(r) > 0]
    vals = [(lab, val) for lab, val in pairs if val]
    if not vals:
        return []
    code = next((v for _, v in vals if _ID_PAT.search(v) and len(v) <= 16), "")
    detail_val = max((v for _, v in vals), key=len)                    # 세부내용 = 최장값
    name_cand = [v for _, v in vals if v != code and v != detail_val and not _NUM_ONLY.match(v)]
    name = min(name_cand, key=len) if name_cand else ""                # 요구사항명 ≈ 최단 제목값
    out = []
    for piece in split_items(detail_val):
        out.append({"level": name or code, "name": name, "detail": piece, "code_hint": code})
    return out


def _looks_header(row: list[str]) -> bool:
    """헤더행 판정 — 셀이 모두 짧고(≤12) 서술문이 아님."""
    cells = [_norm(c) for c in row if _norm(c)]
    if len(cells) < 2:
        return False
    return all(len(c) <= 12 for c in cells)


def _horizontal_reqs(grid: list[list[str]], ncol: int, stats: list[dict]) -> list[dict]:
    """가로 요구표 → 데이터행마다 요구. 내용열=상세(최장), 구분열=계위(rowspan forward-fill)."""
    header = grid[0] if grid and _looks_header(grid[0]) else None
    data = grid[1:] if header else grid
    if not data:
        return []
    # 내용(상세) 열 = 평균 길이 최장
    content = max(range(ncol), key=lambda c: stats[c]["avglen"])
    if stats[content]["avglen"] < 8:          # 서술열이 없음 → 요구표 아님
        return []
    # 계위(구분) 열 = content 왼쪽에서 '숫자 index 아니고 라벨성(짧음)'인 최좌열
    cat_col = None
    for c in range(content):
        if stats[c]["numratio"] > 0.6:        # 순번/No 열 스킵
            continue
        if stats[c]["avglen"] <= 25:
            cat_col = c
            break
    # 요구사항명 열 = content 왼쪽, cat_col 아닌 중간 길이 열(있으면)
    name_col = None
    for c in range(content):
        if c == cat_col or stats[c]["numratio"] > 0.6:
            continue
        if 4 <= stats[c]["avglen"] <= content and c != cat_col:
            name_col = c
            break

    def cell(row, c):
        return _norm(row[c]) if c is not None and c < len(row) else ""

    out: list[dict] = []
    cat_fill = name_fill = ""
    for row in data:
        det = cell(row, content)
        if not det or len(det) < 3:
            continue
        cat = cell(row, cat_col) or cat_fill
        cat_fill = cat or cat_fill
        nm = cell(row, name_col) or name_fill
        name_fill = nm or name_fill
        # _tab = 구분(계위)열 값 → 이 요구표를 ambient 헤딩이 아닌 자기 카테고리로 그룹핑
        # (기아처럼 요구표가 '상담석 규모' 같은 junk 헤딩 아래 misfiled 되는 것 방지)
        tab_hint = re.sub(r"^\s*(?:\d+[-.)]\s*)+", "", cat).strip() or cat
        for piece in split_items(det):
            out.append({"level": cat, "name": nm or cat, "detail": piece, "_tab": tab_hint})
    return out


def table_to_reqs(grid: list[list[str]]) -> list[dict] | None:
    """표 → 요구 dict 리스트. junk면 [](드롭). 요구표 아님(불명확)이면 None(호출측 폴백)."""
    grid = [[_norm(c) for c in r] for r in grid if any(_norm(c) for c in r)]
    if not grid:
        return []
    if is_junk_table(grid):
        return []
    ncol, stats = _col_stats(grid)
    if _is_vertical_card(grid, ncol, stats):
        return _vertical_to_reqs(grid)
    if ncol >= 2:
        rows = _horizontal_reqs(grid, ncol, stats)
        if rows and not _rows_look_like_data_dump(rows):
            return rows
        if rows:
            return []            # 대량 단문 = 현황/규모/집계 데이터표 → 드롭
    return None


def _rows_look_like_data_dump(rows: list[dict]) -> bool:
    """추출된 행들이 요구사항이 아니라 현황/규모/집계 데이터표인지 — 상세가 대부분 단문."""
    lens = sorted(len(r["detail"]) for r in rows)
    med = lens[len(lens) // 2] if lens else 0
    if med < 10:                              # 상세 중앙값이 매우 짧음 = 라벨/숫자 격자
        return True
    if len(rows) > 60 and med < 20:           # 대량 + 단문 = 상담석 규모/현황 덤프
        return True
    return False
