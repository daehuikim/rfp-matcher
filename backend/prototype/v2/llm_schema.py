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
from app.llm.openai_client import OpenAIClient

from .extract import Req, parse_cell_hierarchy
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
        "- is_requirement: 제안사가 구축·이행할 시스템/연구개발의 기능·기술 요구사항(연구목표·"
        "목표성능·개발내용 등) 표면 true. **제안서 평가기준·평가항목·배점·심사, 참여인력 명단, "
        "재무·신용평가, 일정·WBS, 목차·연락처·현황** 표면 false.\n"
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
    """스키마대로 결정적으로 행을 만든다(내용은 원문 셀 그대로 이동, 누락·창작 없음)."""
    if not schema.is_requirement:
        return []
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
        # 결정적 carry-forward (병합셀/빈 계위 채움 — LLM 아님)
        if top:
            last_top, last_mid = top, ""   # 대분류 바뀌면 중분류 carry 초기화
        else:
            top = last_top
        if mid:
            last_mid = mid
        else:
            mid = last_mid
        page = grid.page_of(r)
        kind = "리스트" if grid.table_id < 0 else f"표#{grid.table_id}"
        src = f"p.{page} · {kind}" if page else kind
        # 셀 내부 □/∙/- 계위를 atomic 행으로 분할(셀 내 □ 그룹 carry-forward)
        cell_group = ""
        for group, sub in parse_cell_hierarchy(detail):
            if group:
                cell_group = group
            g = group or cell_group
            if g:  # 셀 내부 □ 그룹 → 요구사항, 컬럼 요구사항은 항목명으로 흡수
                rtop = f"{top} / {mid}".strip(" /") if mid else top
                rmid = g
            else:
                rtop, rmid = top, mid
            out.append(Req(
                doc=doc_name, table_id=grid.table_id, page=page,
                top=norm(rtop), mid=norm(rmid), detail=norm(sub),
                section_path=sp, source=src,
            ))
    return out


async def _design_one(client: OpenAIClient, sem: asyncio.Semaphore,
                      grid: Grid, section: str) -> list[Req]:
    async with sem:
        try:
            schema = await client.structured_output(
                [Message(role="user", content=_prompt(section, _grid_text(grid), grid.ncols))],
                TableSchema, purpose="schema_design", max_tokens=2000)
        except Exception:
            return []
    return execute_schema("", grid, schema, section)


async def schema_extract_tables(candidates: list[tuple[Grid, str]],
                                concurrency: int = 6) -> list[Req]:
    if not candidates:
        return []
    s = Settings()
    client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)
    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[_design_one(client, sem, g, sec) for g, sec in candidates])
    return [r for group in results for r in group]


def schema_extract_tables_sync(candidates: list[tuple[Grid, str]]) -> list[Req]:
    return asyncio.run(schema_extract_tables(candidates))
