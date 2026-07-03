"""스펙 4단계 — 조견표 룰(표룰/본문룰/특수룰/atomic). 특수룰 > 일반룰.

표룰: ref열(수량/비고/번호/No.)을 먼저 참조사항으로 분리(특수룰) → 잔여 열수로 일반룰
  (1→상세, 2→요구사항/상세, 3→항목명/요구사항/상세, 4→항목명/요구사항/상세/상세, 5+→LLM).
본문룰: (임시 특수룰) 라인/불릿 단위로 상세요건. Subject 있으면 요구사항으로 승격.
atomic: 상세요건은 라인-선두 불릿 단위로 분리(중간참조 불릿은 분리 안 함).
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .db import Row, HeadingStack
from .table_post import Table

# ── atomic 분리 (기타 특수룰) ───────────────────────────────────────────────
# 라인-선두 불릿/번호(계층형) — 명사연결 '-'(A-B)·중간참조(문장 속 '1.2.')와 구분
_LINE_BULLET = re.compile(r"^\s*(?:[-*∙•·–—○◦●❍▪◇■□]\s+|[가-힣]\.\s+|\d+[.)]\s+|[①-⑳]\s*)")


def atomic_split(text: str) -> list[str]:
    """상세요건 atomic 분리 — 라인 단위 + 라인-선두 불릿 단위. 중간참조/명사연결 - 는 미분리."""
    text = (text or "").strip()
    if not text:
        return []
    parts: list[str] = []
    for raw in re.split(r"[\n\r]+", text):
        line = raw.strip()
        if not line:
            continue
        # 한 라인 안에 라인-선두형 불릿이 여러 개 이어붙은 경우만 분할
        segs = re.split(r"(?<=\S)\s+(?=(?:[○◦●❍▪◇■□]\s+|\d+[.)]\s+|[가-힣]\.\s+))", line)
        for s in segs:
            s = s.strip()
            if s:
                parts.append(s)
    return parts


def _strip_bullet(s: str) -> str:
    return _LINE_BULLET.sub("", s).strip()


# ── 표 컬럼 역할 분류 ───────────────────────────────────────────────────────
_REF_TERMS = ("수량", "비고", "번호", "no", "no.", "단위", "순번", "연번", "페이지", "쪽")


def _ref_cols(header: list[str] | None, ncols: int) -> set[int]:
    """헤더에 수량/비고/번호/No. 가 든 칼럼 → 참조사항(특수룰)."""
    if not header:
        return set()
    out = set()
    for c in range(ncols):
        h = (header[c] if c < len(header) else "").strip().lower()
        if h and any(t == h or t in h for t in _REF_TERMS) and len(h) <= 6:
            out.add(c)
    return out


class _ColResult(BaseModel):
    requirement: str   # 상위 구분 → 요구사항
    detail: str        # 상세 스펙 → 상세요건


class _LLMRows(BaseModel):
    rows: list[_ColResult]


def _llm_table(table: Table) -> list[tuple[str, str]]:
    """5단+ 표(현황/HW/SW 스펙) → LLM 해석. 상위구분=요구사항, 상세스펙=상세요건."""
    from app.core.config import Settings
    from app.llm.base import Message
    from app.llm.factory import build_llm_client
    from app.llm.fake_client import FakeLlmClient
    from prototype.v2.async_run import run_coro

    client = build_llm_client(Settings())
    if isinstance(client, FakeLlmClient):
        # 폴백(키없음): 1열=요구사항, 나머지 concat=상세
        out = []
        for row in table.rows[max(table.header_row, 0) + (1 if table.header_row >= 0 else 0):]:
            out.append((row[0] if row else "", " / ".join(c for c in row[1:] if c)))
        return out
    body = table.rows[(table.header_row + 1) if table.header_row >= 0 else 0:]
    header = table.rows[table.header_row] if table.header_row >= 0 else []
    block = "\n".join(" | ".join(r) for r in body[:60])
    prompt = (
        "다음은 RFP 의 여러 칼럼 표(현황·HW/SW 스펙 가능성 높음)다. 각 행을 조견표 2단으로 해석하라.\n"
        "- requirement(요구사항): 그 행의 상위 구분/항목(짧은 명사구)\n"
        "- detail(상세요건): 상세 스펙/내용 전체를 한 문자열로\n"
        f"헤더: {' | '.join(header)}\n[행]\n{block}\n\n"
        'JSON: {"rows":[{"requirement":"..","detail":".."}, ...]} — 모든 행.'
    )

    async def _go():
        return await client.structured_output([Message(role="user", content=prompt)], _LLMRows,
                                              purpose="vrule_table5", max_tokens=8000)
    try:
        res = run_coro(_go())
        return [(r.requirement, r.detail) for r in res.rows]
    except Exception:
        return [((row[0] if row else ""), " / ".join(c for c in row[1:] if c)) for row in body]


def table_to_rows(table: Table, stack: HeadingStack) -> list[Row]:
    """표 → 조견표 행들(표 일반룰 + 특수룰)."""
    n = table.ncols
    if n == 0:
        return []
    header = table.rows[table.header_row] if table.header_row >= 0 else None
    data = table.rows[(table.header_row + 1):] if table.header_row >= 0 else table.rows
    refset = _ref_cols(header, n)
    eff = [c for c in range(n) if c not in refset]   # 잔여(참조 제외) 칼럼

    def base() -> Row:
        return Row(part=stack.part, section=stack.section, subject=stack.subject,
                   sub_subject=stack.sub_subject, origin="table",
                   table_id=id(table) % 100000)

    rows: list[Row] = []

    # 5단+ → LLM
    if len(eff) >= 5:
        for req, det in _llm_table(table):
            for d in (atomic_split(det) or [det]):
                r = base(); r.requirement = req; r.detail = d
                r.item = stack.subject or stack.sub_subject
                rows.append(r)
        return rows

    # 일반룰 by 잔여 열수
    for drow in data:
        cells = [(drow[c].strip() if c < len(drow) else "") for c in range(n)]
        ref = " / ".join(cells[c] for c in sorted(refset) if cells[c])
        e = [cells[c] for c in eff]
        if not any(e):
            continue
        item = req = det = ""
        if len(eff) == 1:
            det = e[0]; item = stack.subject or stack.sub_subject
        elif len(eff) == 2:
            req, det = e[0], e[1]; item = stack.subject or stack.sub_subject
        elif len(eff) == 3:
            item, req, det = e[0], e[1], e[2]
        elif len(eff) == 4:
            item, req = e[0], e[1]; det = (e[2] + (" / " + e[3] if e[3] else "")).strip()
        for d in (atomic_split(det) or [det]):
            r = base(); r.item, r.requirement, r.detail, r.ref = item, req, d, ref
            rows.append(r)
    return rows


# ── 본문룰(임시 특수룰: 라인/불릿 → 상세요건) ────────────────────────────────
def body_to_rows(lines: list[str], stack: HeadingStack, page: int | None = None) -> list[Row]:
    """본문 블록(라인 리스트) → 조견표 행. 임시룰: 각 라인 = 상세요건.

    Subject/Sub-Subject 있으면 요구사항으로 승격(본문 일반룰1). 상세 비면 계층 끌어내림.
    """
    rows: list[Row] = []
    for line in lines:
        for d in (atomic_split(line) or [line]):
            d = d.strip()
            if not d:
                continue
            r = Row(part=stack.part, section=stack.section, subject=stack.subject,
                    sub_subject=stack.sub_subject, origin="body", page=page)
            r.detail = _strip_bullet(d)
            if stack.has_subject():
                r.requirement = stack.sub_subject or stack.subject
            rows.append(r)
    return rows
