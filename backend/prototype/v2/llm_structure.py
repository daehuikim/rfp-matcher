"""
LLM 표 구조화 패스 — messy 표를 LLM이 읽어 조견표 계위(항목명/요구사항/상세)로 정규화.

핵심 안전장치(할루시 방지, 스키마 기반):
  1) 출력 스키마 고정: is_requirement(bool) + rows[{top, mid, detail}].
  2) **원문 검증**: 각 행의 detail 은 원본 표 텍스트의 부분문자열일 때만 채택.
     top/mid 도 원문에 없으면 비움(창작 금지). → 모델이 새 문장을 지어내면 드롭.
  3) 비요구 표(현황/스펙목록/목차/연락처/가격)는 is_requirement=false → 행 없음.

LLM 은 '읽고 계위만 재배치'할 뿐, 텍스트는 원문에서만 온다.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.factory import build_llm_client

from .extract import Req
from .grid import Grid
from .text import norm, sig


class _Row(BaseModel):
    top: str    # 항목명(대분류)
    mid: str    # 요구사항(중분류/제목)
    detail: str  # 상세요건(구체 내용)


class _StructResult(BaseModel):
    is_requirement: bool
    rows: list[_Row]


def _grid_text(grid: Grid, max_rows: int = 60) -> str:
    lines = []
    for r in range(min(grid.nrows, max_rows)):
        cells = [c for c in grid.cells[r] if c]
        if cells:
            lines.append(" | ".join(c[:120] for c in cells))
    return "\n".join(lines)


def _prompt(section: str, grid_text: str) -> str:
    return (
        f"다음은 RFP 문서의 표다(섹션: {section[:60]}). 이 표를 요구사항 조견표 행으로 "
        "정규화하라.\n"
        "■ is_requirement: 이 표가 '제안사가 구축·이행할 시스템의 기능·기술 요구사항'을 "
        "담으면 true. 발주사 현황·HW/제품 스펙목록·목차·연락처·일정·가격·조직 등이면 "
        "false(rows 는 빈 배열).\n"
        "■ 각 행 스키마: top=항목명(대분류 그룹), mid=요구사항(중분류 제목), "
        "detail=상세요건(구체 내용).\n"
        "■ **표의 데이터 행 1개 = 출력 행 1개**가 원칙. 표의 컬럼들을 top/mid/detail 에 "
        "매핑한다(예: [구분|주요 역할|제안 기준] → top=구분값, mid=주요역할값, detail=제안기준값). "
        "**컬럼 헤더 라벨('주요 역할','제안 기준' 등) 자체를 값으로 넣지 말 것.** "
        "병합으로 위 행과 같은 그룹이면 top 을 이어받는다.\n"
        "■ 헤더 행(컬럼명만 있는 행)·번호만 있는 행·안내문장은 행으로 만들지 않는다.\n"
        "■ **원문 텍스트를 그대로 사용**한다. 요약·바꿔쓰기·새 문장 창작 절대 금지 "
        "(표에 없는 말을 지어내면 안 됨).\n\n"
        f"[표]\n{grid_text}\n\n"
        '응답 JSON: {"is_requirement": <bool>, '
        '"rows": [{"top": "...", "mid": "...", "detail": "..."}, ...]}'
    )


def _keep_text(value: str, src_sig: str) -> str:
    """원문에 있는 텍스트만 통과(할루시 방지). 짧은 라벨은 관대."""
    s = sig(value)
    if not s:
        return ""
    if s in src_sig:
        return norm(value)
    return ""  # 원문에 없음 → 창작으로 간주, 버림


async def _structure_one(client, sem: asyncio.Semaphore,
                         grid: Grid, section: str) -> list[Req]:
    gtext = _grid_text(grid)
    src_sig = sig(" ".join(c for row in grid.cells for c in row))
    async with sem:
        try:
            out = await client.structured_output(
                [Message(role="user", content=_prompt(section, gtext))],
                _StructResult, purpose="structure_table", max_tokens=12000)
        except Exception:
            return []
    if not out.is_requirement:
        return []
    from .extract import format_source

    src = format_source(table_id=grid.table_id, page=grid.page, section=section)
    reqs: list[Req] = []
    for row in out.rows:
        detail = _keep_text(row.detail, src_sig)
        if len(sig(detail)) < 4:  # detail 이 원문에 없으면(할루시) 드롭
            continue
        reqs.append(Req(
            doc="", table_id=grid.table_id, page=grid.page,
            top=_keep_text(row.top, src_sig), mid=_keep_text(row.mid, src_sig),
            detail=detail, section_path=norm(section), source=src,
        ))
    return reqs


async def structure_tables(candidates: list[tuple[Grid, str]],
                           concurrency: int = 6) -> list[Req]:
    if not candidates:
        return []
    s = Settings()
    client = build_llm_client(s)
    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[_structure_one(client, sem, g, sec) for g, sec in candidates])
    return [r for group in results for r in group]


def structure_tables_sync(candidates: list[tuple[Grid, str]]) -> list[Req]:
    return asyncio.run(structure_tables(candidates))
