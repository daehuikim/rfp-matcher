"""
셀 단위 LLM 분류 — 각 CellUnit → 요구사항 행 1건 (또는 skip).

배치가 아닌 unit-by-unit 병렬 호출 (Semaphore).
"""
from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.llm.base import Message
from app.llm.factory import build_llm_client

from prototype.v2.extract import Req, format_source
from prototype.v2.text import norm, sig

from .cell_chunker import CellUnit

logger = logging.getLogger(__name__)

_SKIP_KINDS = frozenset({"image", "nested_table"})


class UnitRow(BaseModel):
    is_requirement: bool
    tab: str = ""
    top: str = Field(default="", description="항목명 — 첫 행만, 연속은 빈칸")
    mid: str = Field(default="", description="요구사항 제목")
    detail: str = Field(default="", description="상세 본문 — 원문 그대로")


def _prompt(unit: CellUnit, doc_title: str) -> str:
    ctx = f"섹션: {unit.section[:80]}\n" if unit.section else ""
    span = ""
    if unit.meta.get("rowspan") or unit.meta.get("colspan"):
        span = f" (rowspan={unit.meta.get('rowspan',1)}, colspan={unit.meta.get('colspan',1)})"
    return (
        f"RFP 문서 '{doc_title}' 의 표 셀에서 추출한 **한 단위** 텍스트다.\n"
        f"{ctx}"
        f"위치: 표#{unit.table_id} 행{unit.row} 열{unit.col}{span}\n"
        f"종류: {unit.kind}\n\n"
        f"[텍스트]\n{unit.text[:1200]}\n\n"
        "이 단위가 **제안사가 구축·이행해야 할 요구사항**이면 is_requirement=true.\n"
        "false: 목차·현황·H/W사양·배점·연락처·단순 나열(모니터링 도구명만) 등.\n"
        "true이면:\n"
        "- tab: 문서 섹션 기반 탭명(예: 제안개요, ICT, 정보보호)\n"
        "- top: 큰 도메인 항목명(해당 없으면 '')\n"
        "- mid: 서브그룹 제목(해당 없으면 '')\n"
        "- detail: **원문 그대로** (수정·요약 금지)\n\n"
        'JSON: {"is_requirement": <bool>, "tab": "", "top": "", "mid": "", "detail": ""}'
    )


async def _classify_one(
    client,
    sem: asyncio.Semaphore,
    unit: CellUnit,
    doc_title: str,
) -> tuple[CellUnit, UnitRow | None]:
    if unit.kind in _SKIP_KINDS:
        return unit, None
    if len(sig(unit.text)) < 3:
        return unit, None
    async with sem:
        try:
            row = await client.structured_output(
                [Message(role="user", content=_prompt(unit, doc_title))],
                UnitRow,
                purpose="cell_unit",
                max_tokens=1500,
            )
            from app.services.pipeline_logger import record_llm_io

            record_llm_io(
                "cell_unit",
                prompt=_prompt(unit, doc_title),
                response=row,
                meta={"uid": unit.uid, "kind": unit.kind},
            )
            return unit, row
        except Exception:
            logger.warning("cell_unit LLM 실패 uid=%s", unit.uid, exc_info=True)
            return unit, None


def _dedupe_units(units: list[CellUnit]) -> list[CellUnit]:
    """rowspan 등으로 동일 텍스트 중복 unit 제거."""
    seen: set[tuple[str, str]] = set()
    out: list[CellUnit] = []
    for u in units:
        key = (u.kind, sig(u.text))
        if key in seen or len(sig(u.text)) < 3:
            continue
        seen.add(key)
        out.append(u)
    return out


async def units_to_reqs(
    units: list[CellUnit],
    *,
    doc_name: str,
    concurrency: int = 32,
) -> list[Req]:
    units = _dedupe_units(units)
    s = Settings()
    client = build_llm_client(s)
    sem = asyncio.Semaphore(concurrency)
    pairs = await asyncio.gather(
        *[_classify_one(client, sem, u, doc_name) for u in units]
    )
    reqs: list[Req] = []
    for unit, row in pairs:
        if row is None or not row.is_requirement:
            continue
        detail = norm(row.detail or unit.text)
        if len(sig(detail)) < 3:
            continue
        tab = norm(row.tab) or _guess_tab(unit.section) or "요구사항"
        src = format_source(table_id=unit.table_id, page=None, section=unit.section)
        src += f" · {unit.uid}"
        reqs.append(
            Req(
                doc=doc_name,
                table_id=unit.table_id,
                page=None,
                top=norm(row.top),
                mid=norm(row.mid),
                detail=detail,
                source=src,
                tab=tab,
            )
        )
    return reqs


def units_to_reqs_sync(units: list[CellUnit], *, doc_name: str, concurrency: int = 24) -> list[Req]:
    from prototype.v2.async_run import run_coro

    return run_coro(units_to_reqs(units, doc_name=doc_name, concurrency=concurrency))


def _guess_tab(section: str) -> str:
    s = norm(section)
    if not s:
        return ""
    # 섹션 번호 앞부분만 탭 힌트
    for sep in (".", " "):
        if sep in s:
            return s.split(sep)[0].strip()[:40]
    return s[:40]
