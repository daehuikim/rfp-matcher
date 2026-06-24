"""
LLM 스키마 설계 + 결정적 실행.

사용자 요구 구조:
  1) LLM 이 candidate(표)를 읽고
  2) **스키마만 설계**(어떤 컬럼=무슨 역할, 헤더 몇 행, 요구사항 표인지, 도메인)
  3) 결정적 executor 가 그 스키마대로 내용을 '옮기기만' 한다.

→ LLM 출력은 스키마(작음)뿐. 행을 LLM 이 나열하지 않으므로 누락·할루시 0.
   내용 이동은 100% 결정적(원문 셀 그대로).
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.factory import build_llm_client

from .blocks import _keep
from .extract import Req, format_source, parse_cell_hierarchy
from .grid import Grid
from .text import norm, sig

ROLE_TOP, ROLE_MID, ROLE_DETAIL, ROLE_IGNORE = "항목명", "요구사항", "상세요건", "무시"


class _ColumnSpec(BaseModel):
    index: int
    role: str  # 항목명 | 요구사항 | 상세요건 | 무시


class TableSchema(BaseModel):
    is_requirement: bool
    header_rows: int          # 맨 위 헤더(컬럼명) 행 수
    domain: str               # 표 내용 도메인(탭 후보)
    columns: list[_ColumnSpec]


def _grid_text(grid: Grid, max_rows: int = 40) -> str:
    head = " | ".join(f"c{i}" for i in range(grid.ncols))
    lines = [f"      {head}"]
    for r in range(min(grid.nrows, max_rows)):
        cells = [grid.cells[r][c][:80] for c in range(grid.ncols)]
        lines.append(f"[행{r}] " + " | ".join(cells))
    return "\n".join(lines)


def _prompt(section: str, grid_text: str, ncols: int) -> str:
    return (
        f"다음은 RFP 문서의 표다(섹션: {section[:60]}). 이 표를 요구사항 조견표로 옮기기 "
        "위한 **스키마만 설계**하라. 행 내용은 절대 출력하지 말고 스키마만:\n"
        "- is_requirement: 제안사가 구축·이행할 기능·기술 **요구사항** 표면 true. "
        "false: **발주사 현황·H/W·서버 사양·장비목록(□K8S Worker/CPU/GPU/MEM), "
        "제안평가·배점, 인력·재무, 일정·WBS, 목차·연락처, 체크리스트 스펙만 있는 표**.\n"
        "- header_rows: 맨 위 '컬럼명만 있는' 헤더 행 수(보통 1, 없으면 0).\n"
        "- domain: 이 표 내용의 도메인 명사구(예: 정보보호, 데이터, ICT 인프라, 기능).\n"
        f"- columns: c0..c{ncols - 1} 각 컬럼의 역할 — '{ROLE_TOP}'(대분류 그룹), "
        f"'{ROLE_MID}'(중분류 제목), '{ROLE_DETAIL}'(구체 내용 본문), '{ROLE_IGNORE}'(번호/빈열 등). "
        "본문이 가장 긴 컬럼이 보통 상세요건. 표마다 컬럼 수·의미가 다르니 내용을 보고 정하라.\n\n"
        f"[표]\n{grid_text}\n\n"
        '응답 JSON: {"is_requirement": <bool>, "header_rows": <int>, "domain": "<도메인>", '
        '"columns": [{"index": <int>, "role": "<역할>"}, ...]}'
    )


def execute_schema(doc_name: str, grid: Grid, schema: TableSchema, section: str) -> list[Req]:
    """스키마대로 결정적으로 행을 만든다(내용은 원문 셀 그대로 이동, 누락·창작 없음).

    is_requirement=false 여도 추출한다(100% recall) — 요구/비요구 판정은 섹션단위 keep 이 담당.
    """
    role_of = {c.index: c.role for c in schema.columns}

    def col(role: str) -> int | None:
        for i, r in role_of.items():
            if r == role and 0 <= i < grid.ncols:
                return i
        return None

    det = col(ROLE_DETAIL)
    if det is None:  # detail 미지정 → 평균 길이 최장 컬럼으로 보정
        avgs = [(sum(len(grid.cells[r][c]) for r in range(grid.nrows)) / max(1, grid.nrows), c)
                for c in range(grid.ncols)]
        det = max(avgs)[1] if avgs else 0
    top_c, mid_c = col(ROLE_TOP), col(ROLE_MID)

    out: list[Req] = []
    last_top = last_mid = ""
    # section_path = 문서 섹션 heading 우선(ordered 탭은 문서구조 따름). 없으면 domain.
    sp = norm(section or schema.domain)
    for r in range(max(0, schema.header_rows), grid.nrows):
        row = grid.cells[r]
        detail = row[det] if det < len(row) else ""
        if len(sig(detail)) < 2:
            continue
        top = row[top_c] if top_c is not None and top_c < len(row) and row[top_c] else ""
        mid = row[mid_c] if mid_c is not None and mid_c < len(row) and row[mid_c] else ""
        if top:
            last_top, last_mid = top, ""
        else:
            top = last_top
        if mid:
            last_mid = mid
        else:
            mid = last_mid
        page = grid.page_of(r)
        src = format_source(table_id=grid.table_id, page=page, section=section)
        # □ 그룹 단위 1행; 같은 그룹/중분류의 ∙·- 자식은 detail 줄바꿈(과분할 방지)
        buckets: list[tuple[str, list[str]]] = []
        cur_mid = mid
        cur_parts: list[str] = []

        def flush() -> None:
            nonlocal cur_parts, cur_mid
            if not cur_parts:
                return
            buckets.append((cur_mid, cur_parts[:]))
            cur_parts = []

        for group, sub in parse_cell_hierarchy(detail):
            if group:
                flush()
                cur_mid = group
                cur_parts = [sub]
            else:
                if not cur_parts:
                    cur_mid = mid
                cur_parts.append(sub)
        flush()

        for bmid, parts in buckets:
            body = "\n".join(parts)
            use_top = norm(top)
            use_mid = norm(bmid) if bmid else norm(mid)
            if use_mid and use_mid in use_top:
                use_mid = norm(mid) or use_mid
            nr = Req(
                doc=doc_name, table_id=grid.table_id, page=page,
                top=use_top, mid=use_mid, detail=norm(body),
                section_path=sp, source=src,
            )
            out.append(nr)  # 100% recall — 행단위 노이즈 드롭 안 함(boilerplate는 섹션 keep이 섹션단위로)
    return out


def execute_deferred_list_block(doc_name: str, grid: Grid, section: str) -> list[Req]:
    """defer_tables 리스트 블록(1열, table_id<0) — 결정적 추출(100% recall).

    섹션 키워드 게이트(section_has_requirement_context) 제거 — '기타사항'처럼 어휘에 없는
    요구 섹션이 통째 누락되던 문제 방지. boilerplate(목차/배경/일정/입찰/서식) 섹션 제외는
    다운스트림 LLM keep(llm_tabs.assign_tabs)이 섹션단위로 판단(키워드 하드코딩 대신 LLM).
    """
    sp = norm(section)
    out: list[Req] = []
    for r in range(grid.nrows):
        detail = grid.cells[r][0] if grid.ncols else ""
        if not _keep(detail):  # 빈/초단편만 거름. is_noise_detail(HW사양 등) 드롭 제거 — 100% recall
            continue
        page = grid.page_of(r)
        src = format_source(table_id=grid.table_id, page=page, section=section) + " · 리스트"
        out.append(Req(
            doc=doc_name,
            table_id=grid.table_id,
            page=page,
            top="",
            mid="",
            detail=norm(detail),
            section_path=sp,
            source=src,
        ))
    return out


async def _design_one(client, sem: asyncio.Semaphore,
                      grid: Grid, section: str) -> list[Req]:
    if grid.ncols == 1 and grid.table_id < 0:
        return execute_deferred_list_block("", grid, section)
    # grid_looks_like_inventory 드롭 제거 — '서버 요구 사항'(HW스펙) 같은 요구표가 inventory 로
    # 오판돼 통째 누락되던 문제(100% recall). 발주사 현황 섹션은 섹션 keep 이 섹션단위로 제외.
    async with sem:
        try:
            prompt = _prompt(section, _grid_text(grid), grid.ncols)
            schema = await client.structured_output(
                [Message(role="user", content=prompt)],
                TableSchema, purpose="schema_design", max_tokens=2000)
            from app.services.pipeline_logger import record_llm_io

            record_llm_io(
                "schema_design",
                prompt=prompt,
                response=schema,
                meta={"section": section[:80], "table_id": grid.table_id, "ncols": grid.ncols},
            )
        except Exception:
            return []
    return execute_schema("", grid, schema, section)  # is_requirement 무관 추출(섹션 keep이 판정)


async def schema_extract_tables(candidates: list[tuple[Grid, str]],
                                concurrency: int = 6) -> list[Req]:
    if not candidates:
        return []
    s = Settings()
    client = build_llm_client(s)
    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[_design_one(client, sem, g, sec) for g, sec in candidates])
    return [r for group in results for r in group]


def schema_extract_tables_sync(candidates: list[tuple[Grid, str]]) -> list[Req]:
    from .async_run import run_coro
    return run_coro(schema_extract_tables(candidates))
