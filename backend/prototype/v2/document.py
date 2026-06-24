"""
문서 순서 1-패스 추출 — heading 계층을 추적하며 표·리스트를 함께 수집.

opendataloader heading 은 level 을 안 주지만 텍스트 번호("I."/"2."/"2.6.")가
계층을 인코딩한다. 이를 파싱해 heading 스택을 유지하고, 각 요구사항에
section_path(탭 묶기 입력)와 source(출처 한 칼럼)를 부착한다.

표 연속(페이지 분할) 역할 상속은 extract_grids 와 동일 규칙. 리스트는
요구사항 섹션 heading 아래일 때만 수집(blocks 규칙 재사용).
"""
from __future__ import annotations

import re

from .blocks import _contents, _keep
from .classify import classify, detect_header_row, is_requirement_table
from .extract import Req, atomize_detail, clean_heading, format_source, level_table
from .grid import Grid, from_opendataloader_table
from .schema import is_requirement_section, section_has_requirement_context
from .text import norm, sig

_ROMAN = re.compile(r"^\(?[IVXLCDM]+[.)]\s")
_ARABIC = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
_BULLET = re.compile(r"^[▪○●◦∙·O\-–—]\s?")


def heading_depth(title: str) -> int:
    """heading 텍스트 번호로 계층 깊이 추정. 로마=1, '2.'=2, '2.6.'=3 ..."""
    t = norm(title)
    if _ROMAN.match(t):
        return 1
    m = _ARABIC.match(t)
    if m:
        return 1 + len(m.group(1).split("."))
    if _BULLET.match(t):
        return 6
    return 5


def _heading_title(el: dict) -> str:
    parts = _contents(el)
    return parts[0] if parts else ""


def _section_path(stack: dict[int, str]) -> str:
    return " > ".join(stack[d] for d in sorted(stack))


def extract_document(doc_name: str, doc: dict, mode: str = "fine",
                     defer_tables: bool = False) -> tuple[list[Req], list[tuple]]:
    """문서 트리를 순서대로 재귀 DFS — 중첩된 표·리스트까지 heading 맥락과 함께 수집.

    defer_tables=True: 표는 leveling 하지 않고 (grid, 섹션) candidate 로 모아 반환
    (연속표는 한 grid 로 병합). LLM 구조화 패스용. 리스트는 그대로 deterministic.
    반환: (reqs, table_candidates)
    """
    stack: dict[int, str] = {}
    out: list[Req] = []
    candidates: list[tuple] = []
    # 표 연속(페이지 분할) 상태 — 가변 컨테이너로 클로저 공유
    st = {"roles": None, "ncols": None, "next": None, "cand": None}

    def handle_table(el: dict) -> None:
        grid = from_opendataloader_table(el)
        header_row = detect_header_row(grid)
        nearest = stack[max(stack)] if stack else ""

        if defer_tables:
            cand_g = st["cand"]
            cont = (cand_g is not None and cand_g.ncols == grid.ncols
                    and (st["next"] == grid.table_id or header_row < 0))
            if cont:
                cand_g.cells.extend(grid.cells)  # 연속표 병합
                cand_g.row_pages.extend(grid.row_pages)
                st["next"] = grid.next_id
                return
            roles = classify(grid, header_row)
            if not is_requirement_table(grid, roles, header_row):
                st["cand"] = None
                return
            merged = Grid(cells=[row[:] for row in grid.cells],
                          table_id=grid.table_id, page=grid.page, next_id=grid.next_id,
                          row_pages=list(grid.row_pages))
            sp = _section_path(stack) or nearest
            candidates.append((merged, sp))
            st["cand"], st["next"] = merged, grid.next_id
            return

        roles: dict[str, int] | None = None
        if header_row >= 0:
            cand = classify(grid, header_row)
            if is_requirement_table(grid, cand, header_row):
                roles = cand
        else:
            linked = st["next"] == grid.table_id
            adjacent = st["roles"] is not None and st["ncols"] == grid.ncols
            if st["roles"] is not None and (linked or adjacent):
                roles = st["roles"]
            else:
                cand = classify(grid, header_row)
                if is_requirement_table(grid, cand, header_row):
                    roles = cand
        if roles is not None and "detail" in roles:
            sp = _section_path(stack)
            for r in level_table(doc_name, grid, roles, header_row, nearest, mode):
                r.section_path = sp
                out.append(r)
            st["roles"], st["ncols"], st["next"] = roles, grid.ncols, grid.next_id
        else:
            st["roles"] = st["ncols"] = st["next"] = None

    def flush_listblock() -> None:
        """모아둔 리스트 항목들을 1열 grid candidate 로 — LLM(schema)이 is_requirement 판정."""
        items = st.get("lb") or []
        if not items:
            return
        cells = [[t] for t, _p in items]
        pages = [p for _t, p in items]
        g = Grid(cells=cells, table_id=st["lb_id"], page=pages[0],
                 row_pages=pages)
        candidates.append((g, st.get("lb_section", "")))
        st["lb"] = []
        st["lb_section"] = ""
        st["lb_id"] -= 1

    def _defer_text_block(el: dict) -> None:
        """리스트·문단을 1열 candidate 로 누적.

        100% recall — 섹션 키워드 게이트(section_has_requirement_context) 제거.
        '기타사항' 처럼 어휘에 없는 요구 섹션이 통째 누락되던 문제를 막는다. 요구사항영역 vs
        boilerplate(목차/배경/일정/입찰/서식) 판정은 다운스트림 LLM keep(llm_tabs.assign_tabs)이
        담당 — 키워드 하드코딩 대신 LLM이 섹션을 읽고 판단(원칙 준수).
        """
        sp = _section_path(stack)
        if st.get("lb") and st.get("lb_section") and st["lb_section"] != sp:
            flush_listblock()
        page = el.get("page number")
        if not st.get("lb_section"):
            st["lb_section"] = sp
        for text in _contents(el):
            if _keep(text):
                st.setdefault("lb", []).append((text, page))

    def handle_list_defer(el: dict) -> None:
        _defer_text_block(el)

    def handle_paragraph_defer(el: dict) -> None:
        _defer_text_block(el)

    def handle_list(el: dict) -> None:
        if not stack:
            return
        nearest = stack[max(stack)]
        if not is_requirement_section(nearest):
            return
        sp = _section_path(stack)
        page = el.get("page number")
        src = format_source(table_id=-1, page=page, section=sp)
        ch = clean_heading(nearest)
        top = ch if (ch and len(ch) <= 20) else ""
        for text in _contents(el):
            if not _keep(text):
                continue
            for piece in atomize_detail(text, mode):
                if len(sig(piece)) < 2:
                    continue
                out.append(Req(
                    doc=doc_name, table_id=-1, page=page,
                    top=top, mid="", detail=piece,
                    section_path=sp, source=src,
                ))

    def walk(nodes: list) -> None:
        for el in nodes:
            if not isinstance(el, dict):
                continue
            t = el.get("type")
            if t == "heading":
                if defer_tables:
                    flush_listblock()   # heading 바뀌면 리스트 블록 마감
                title = _heading_title(el)
                d = heading_depth(title)
                stack_keys = [k for k in stack if k >= d]
                for k in stack_keys:
                    del stack[k]
                stack[d] = norm(title)
                st["roles"] = st["ncols"] = st["next"] = None
            elif t == "table":
                if defer_tables:
                    flush_listblock()
                handle_table(el)
            elif t == "list":
                if defer_tables:
                    handle_list_defer(el)
                else:
                    handle_list(el)
            elif t == "paragraph" and defer_tables:
                handle_paragraph_defer(el)
            else:
                kids = el.get("kids")
                if isinstance(kids, list):
                    walk(kids)

    st["lb"] = []
    st["lb_section"] = ""
    st["lb_id"] = -1
    walk(doc.get("kids", []))
    if defer_tables:
        flush_listblock()
    return out, candidates
