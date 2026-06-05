"""
추출 모듈 — 스키마가 배정한 역할로 행을 읽어 요구사항 레코드 생성.

원자화는 macro 모드(그리드 행 = 요구사항 1건, 뭉침 허용). 줄단위 fine 모드는
별도 splitter 모듈로 교체 가능하게 분리(여기선 macro 만).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .classify import classify, column_stats, detect_header_row, is_requirement_table
from .grid import Grid
from .text import norm, sig

_NUMBERING = re.compile(r"^\s*(?:[IVXLCDM]+|\d+(?:\.\d+)*|[가-힣]|[①-⑩])[.)]\s*")
_TRAIL = re.compile(r"\s*(?:관련\s*)?(?:요구사항|요청사항|요건|사항)\s*$")


def clean_heading(h: str) -> str:
    """섹션 헤딩 → 항목명 후보: 앞 번호·뒤 '요구사항/사항' 제거."""
    h = norm(h)
    for _ in range(3):
        new = _NUMBERING.sub("", h)
        if new == h:
            break
        h = new
    h = _TRAIL.sub("", h).strip()
    return h


@dataclass
class Req:
    doc: str
    table_id: int
    page: int | None
    rid: str = ""           # 요구사항 ID (도메인 접두사 + 일련번호)
    top: str = ""           # 항목명 (최상위 계위 — 표 광역 그룹 또는 섹션 도메인)
    mid: str = ""           # 요구사항 (중간 계위 — 표 그룹 컬럼)
    detail: str = ""        # 상세요건 (행 단위 원자)
    section_path: str = ""  # 문서 heading 계층 경로 (탭 묶기 입력)
    tab: str = ""           # LLM 이 배정한 시트(탭) 이름
    source: str = ""        # 출처 한 칼럼: "p.18 · 표#633" / "p.20 · 리스트"
    # 필드별 AI 생성 여부(원문 추출이 아니라 LLM 이 만든 값 → 셀 색 구분)
    gen_rid: bool = False
    gen_top: bool = False
    gen_mid: bool = False


def _span_breadth(values: list[str]) -> float:
    """연속 동일값 평균 run 길이 — 클수록 광역(병합 폭 넓음) 계위."""
    runs, prev = 0, object()
    for v in values:
        if v != prev:
            runs += 1
            prev = v
    return len(values) / max(1, runs)


def _grouping_columns(grid: Grid, header_row: int, detail_col: int,
                      id_col: int | None) -> list[int]:
    """detail·id 외에 '그룹(계위)' 컬럼 후보를 span 폭 넓은 순으로."""
    cols = []
    for c in range(grid.ncols):
        if c == detail_col or c == id_col:
            continue
        st = column_stats(grid, c, header_row)
        if st.nonempty_ratio < 0.4:
            continue
        vals = [grid.cells[r][c] for r in range(header_row + 1, grid.nrows)]
        cols.append((c, _span_breadth(vals)))
    # 광역(폭 큰)순, 동률은 왼쪽 컬럼 우선
    cols.sort(key=lambda cb: (-cb[1], cb[0]))
    return [c for c, _ in cols]


_FINE_BULLET = re.compile(r"\s*(?=[·•□◦▪○●∙‣])")      # 명확한 불릿 기호 앞 분리
_FINE_NUM = re.compile(r"\s*(?=[①-⑩⓪])")              # 원숫자 앞 분리


# 셀 내부 계위: □■◇◆ = 중분류(그룹), 나머지 = 상세 불릿
_GROUP_MARK = "□■◇◆▣"
_SUB_MARK = "∙•·◦▪○●‣"
_CELL_SPLIT = re.compile(r"\s*([□■◇◆▣∙•·◦▪○●‣①-⑩]|(?<=\s)[-–—](?=\s))\s*")


def parse_cell_hierarchy(cell: str) -> list[tuple[str, str]]:
    """셀의 □/∙/- 내부 계위를 (그룹, 상세) 쌍 목록으로 분해.

    □ X (자식 -/∙ 있음) → (X, 자식) 행들. □ X (자식 없음) → (None, X).
    선두 불릿/평문 → (None, 텍스트). 마커 없으면 [(None, cell)] (분할 안 함).
    """
    parts = _CELL_SPLIT.split(cell)
    segs: list[tuple[str | None, str]] = []
    pre = parts[0].strip()
    if pre:
        segs.append((None, pre))
    for i in range(1, len(parts), 2):
        marker = parts[i]
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if content:
            segs.append((marker, content))
    if len(segs) <= 1:
        return [("", cell.strip())]

    out: list[tuple[str, str]] = []
    group: str | None = None
    group_used = False
    for marker, content in segs:
        if marker and marker in _GROUP_MARK:
            if group is not None and not group_used:
                out.append(("", group))      # 자식 없던 □ → 단독 상세
            group, group_used = content, False
        else:
            if group is not None:
                out.append((group, content))  # □ 그룹의 자식
                group_used = True
            else:
                out.append(("", content))
    if group is not None and not group_used:
        out.append(("", group))

    # 같은 그룹 내 짧은(<10자) 단편은 앞에 병합 — "계정 신청 - 승인 - 삭제" 같은 시퀀스
    # 가 dash 로 과분할된 것 복원(삼성처럼 긴 □ 자식은 유지).
    merged: list[tuple[str, str]] = []
    for g, d in out:
        if merged and merged[-1][0] == (g or "") and len(sig(d)) < 10:
            merged[-1] = (merged[-1][0], f"{merged[-1][1]} {d}")
        else:
            merged.append((g or "", d))
    return merged


def atomize_detail(detail: str, mode: str) -> list[str]:
    """fine=줄바꿈·명확한 불릿(□◦▪·①~⑩) 단위 분리. ' - '는 모호해 분리 안 함(과분할 방지).

    정답의 입도는 사람 판단이라 일정치 않으므로 안전한 마커만 분리한다.
    더 정밀한 입도는 LLM 원자화 패스(macro+LLM)로 별도 처리.
    """
    if mode == "macro":
        return [detail]
    text = detail
    for rx in (_FINE_BULLET, _FINE_NUM):
        text = rx.sub("\n", text)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    # 너무 짧은 조각(연속 꼬리, 예: "∙교체 가능 등")은 앞 항목에 붙임 — 과분할 방지
    merged: list[str] = []
    for ln in lines:
        if merged and len(sig(ln)) < 8:
            merged[-1] = f"{merged[-1]} {ln}"
        else:
            merged.append(ln)
    return merged or [detail]


def level_table(doc_name: str, grid: Grid, roles: dict[str, int], header_row: int,
                section_heading: str, mode: str) -> list[Req]:
    """표 컬럼을 항목명(top)/요구사항(mid)/상세요건(detail) 계위로 적응 매핑."""
    det = roles["detail"]
    id_col = roles.get("id")
    groups = _grouping_columns(grid, header_row, det, id_col)
    top_col = groups[0] if len(groups) >= 2 else None
    mid_col = groups[0] if len(groups) == 1 else (groups[1] if len(groups) >= 2 else None)
    section_top = clean_heading(section_heading)

    src = f"p.{grid.page} · 표#{grid.table_id}" if grid.page else f"표#{grid.table_id}"
    out: list[Req] = []
    for r in range(header_row + 1, grid.nrows):
        row = grid.cells[r]
        detail = row[det] if det < len(row) else ""
        if not detail or len(sig(detail)) < 2:
            continue
        top = row[top_col] if top_col is not None and top_col < len(row) else section_top
        mid = row[mid_col] if mid_col is not None and mid_col < len(row) else ""
        if not top:
            top = section_top
        for piece in atomize_detail(detail, mode):
            if len(sig(piece)) < 2:
                continue
            out.append(Req(
                doc=doc_name, table_id=grid.table_id, page=grid.page,
                top=norm(top), mid=norm(mid), detail=piece, source=src,
            ))
    return out


def extract_grids(doc_name: str, grids: list[Grid], section_heading: str = "",
                  mode: str = "fine") -> list[Req]:
    """헤더 없는 연속표는 직전 헤더 표 역할 상속(detail 컬럼 일관). HTML 경로용."""
    out: list[Req] = []
    prev_roles: dict[str, int] | None = None
    prev_ncols: int | None = None
    prev_next: int | None = None

    from .form_table import detect_form_label_col, pivot_form_table

    for grid in grids:
        # 세로형(form) 표 우선 감지 → pivot
        label_col = detect_form_label_col(grid)
        if label_col is not None:
            pivoted = pivot_form_table(doc_name, grid, label_col, mode)
            if pivoted:
                out.extend(pivoted)
                prev_roles = prev_ncols = prev_next = None
                continue

        header_row = detect_header_row(grid)
        roles: dict[str, int] | None = None
        if header_row >= 0:
            cand = classify(grid, header_row)
            if is_requirement_table(grid, cand, header_row):
                roles = cand
        else:
            linked = prev_next == grid.table_id
            adjacent = prev_roles is not None and prev_ncols == grid.ncols
            if prev_roles is not None and (linked or adjacent):
                roles = prev_roles
            else:
                cand = classify(grid, header_row)
                if is_requirement_table(grid, cand, header_row):
                    roles = cand

        if roles is not None and "detail" in roles:
            out.extend(level_table(doc_name, grid, roles, header_row, section_heading, mode))
            prev_roles, prev_ncols, prev_next = roles, grid.ncols, grid.next_id
        else:
            prev_roles = prev_ncols = prev_next = None

    return out
