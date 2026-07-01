"""카드 유닛 → (gemma 요구사항 판단) → 고정칼럼 행(요구사항ID/명/계위/상세).

흐름(사용자 재설계): HTML → 문서순 블록 → 章(상위)=탭 / 카드(가·나 등)=요구사항명 유닛,
그 아래 본문·표를 상세내용(atomic)으로. gemma 는 **카드가 요구사항인지 판단(keep)** 만 —
내용 생성·키워드 룰 없음. 탭별 ID 접두사(같은 탭=같은 접두사).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from .cards import iter_blocks, marker_level, strip_marker


@dataclass
class Unit:
    tab: str                         # 章(상위 섹션) = Excel 탭
    marker: str
    title: str                       # 요구사항명 후보
    level_path: str                  # 계위(마커 경로)
    details: list[str] = field(default_factory=list)   # 상세내용(atomic)


def _table_details(grid: list[list[str]]) -> list[str]:
    """표 행 → 상세 문자열. 헤더로 보이는 첫 행은 라벨로 접두."""
    out = []
    for row in grid:
        cells = [c.strip() for c in row if c and c.strip()]
        if cells:
            out.append(" | ".join(cells))
    return out


_HEAD_MAX = 3   # 레벨 0~3(Ⅰ/1./1.1/가./ㄱ/1.1.1)=헤딩, 4~5(1)/❍/-)=내용(상세)


def build_units(html: str | None = None, blocks: list | None = None) -> list[Unit]:
    """블록 → 유닛 (헤딩 스택 트리). 헤딩(레벨≤3)마다 유닛 시작, 내용(불릿/❍/표/평문)은
    가장 가까운(깊은) 헤딩에 붙인다. 탭=최상위 章 헤딩. 단일 card_level 함정 회피.

    강원랜드처럼 1.섹션 아래 ❍ 내용, JB처럼 가.섹션 아래 - 내용 — 둘 다 자연히 커버.
    """
    if blocks is None:
        blocks = iter_blocks(html or "")
    stack: list[tuple[int, str]] = []   # (level, title)
    units: list[Unit] = []
    cur: Unit | None = None
    for b in blocks:
        lvl = marker_level(b.text) if b.kind == "text" else None
        if b.kind == "text" and lvl is not None and lvl <= _HEAD_MAX:
            title = strip_marker(b.text)[:60]
            stack = [(l, t) for (l, t) in stack if l < lvl] + [(lvl, title)]
            tab = (stack[0][1] if stack else title)[:40] or "요구사항"
            mk = b.text.split()[0] if b.text.split() else ""
            cur = Unit(tab=tab, marker=mk, title=title,
                       level_path=" > ".join(t for _, t in stack))
            units.append(cur)
        else:
            if cur is None:
                cur = Unit(tab="요구사항", marker="", title="", level_path="")
                units.append(cur)
            if b.kind == "table":
                cur.details += _table_details(b.grid)
            elif b.text.strip():
                cur.details.append(strip_marker(b.text) if marker_level(b.text) else b.text)
    return [u for u in units if u.details]   # 내용 있는 유닛만(빈 章 헤딩 제외)


class _KeepItem(BaseModel):
    index: int
    keep: bool


class _KeepResult(BaseModel):
    items: list[_KeepItem]


def _judge_keep(units: list[Unit]) -> dict[int, bool]:
    """gemma 가 각 유닛이 '제안사 이행 요구사항'인지 keep 판정(과포함 우선)."""
    from app.core.config import Settings
    from app.llm.base import Message
    from app.llm.factory import build_llm_client
    from app.llm.fake_client import FakeLlmClient
    from prototype.v2.async_run import run_coro

    client = build_llm_client(Settings())
    if isinstance(client, FakeLlmClient):
        return {i: True for i in range(len(units))}
    out: dict[int, bool] = {}
    CH = 30
    for k in range(0, len(units), CH):
        chunk = units[k:k + CH]
        block = "\n".join(
            f"[{k+j}] 탭='{u.tab}' 제목='{u.title}' 상세첫줄='{(u.details[0][:60] if u.details else '')}'"
            for j, u in enumerate(chunk)
        )
        prompt = (
            "RFP 카드 유닛들이다. 각 유닛이 **제안사가 이행·수행·준수할 요구사항**이면 keep=true, "
            "표지·목차·배경설명·일정·입찰안내·평가배점·제출양식·발주처 현황 등 비요구면 keep=false.\n"
            "애매하면 keep=true(과포함이 누락보다 낫다).\n\n"
            f"[유닛]\n{block}\n\n"
            'JSON: {"items":[{"index":<int>,"keep":<bool>}, ...]} — 모든 index.'
        )
        try:
            res = run_coro(client.structured_output(
                [Message(role="user", content=prompt)], _KeepResult,
                purpose="card_keep", max_tokens=4000))
            for it in res.items:
                out[it.index] = it.keep
        except Exception:
            for j in range(len(chunk)):
                out[k + j] = True
    return out


def rows_from_units(units: list[Unit], keep: dict[int, bool]) -> list[dict]:
    """유닛 + keep 판정 → 고정칼럼 행. (traced 파이프라인이 단계 분리해 쓰도록 공개)"""
    rows: list[dict] = []
    tab_counter: dict[str, int] = {}
    tab_prefix: dict[str, str] = {}

    def _slug(t: str) -> str:
        # 깔끔한 접두사: 대괄호 내용·번호·마커·목차점선 제거 → 한글/영문만, 짧게.
        t = re.sub(r"[\[\(（【][^\])）】]*[\])）】]", "", t or "")   # [..](..) 제거
        toks = re.findall(r"[A-Za-z가-힣]+", t)                     # 숫자 제외(제안서'2'→제안서)
        return ("".join(toks)[:8]) or "REQ"

    for i, u in enumerate(units):
        if not keep.get(i, True):
            continue
        if u.tab not in tab_prefix:
            tab_prefix[u.tab] = _slug(u.tab)
        pfx = tab_prefix[u.tab]
        for d in (u.details or [u.title]):
            d = re.sub(r"\s+", " ", d).strip()
            if not d:
                continue
            tab_counter[pfx] = tab_counter.get(pfx, 0) + 1
            rows.append({"tab": u.tab, "code": f"{pfx}-{tab_counter[pfx]:03d}",
                         "name": u.title, "level": u.level_path, "detail": d})
    return rows


def extract_fixed_rows(html: str, doc_name: str) -> list[dict]:
    """HTML → 고정칼럼 행 dict 리스트: {tab, code, name, level, detail}. gemma keep 적용."""
    units = build_units(html)
    keep = _judge_keep(units)
    return rows_from_units(units, keep)


def _extract_fixed_rows_legacy(html: str, doc_name: str) -> list[dict]:
    units = build_units(html)
    keep = _judge_keep(units)
    rows: list[dict] = []
    tab_counter: dict[str, int] = {}
    tab_prefix: dict[str, str] = {}

    def _slug(t: str) -> str:
        # 깔끔한 접두사: 대괄호 내용·번호·마커·목차점선 제거 → 한글/영문만, 짧게.
        t = re.sub(r"[\[\(（【][^\])）】]*[\])）】]", "", t or "")   # [..](..) 제거
        toks = re.findall(r"[A-Za-z가-힣]+", t)                     # 숫자 제외(제안서'2'→제안서)
        return ("".join(toks)[:8]) or "REQ"

    for i, u in enumerate(units):
        if not keep.get(i, True):
            continue
        if u.tab not in tab_prefix:
            tab_prefix[u.tab] = _slug(u.tab)
        pfx = tab_prefix[u.tab]
        details = u.details or [u.title]
        for d in details:
            d = re.sub(r"\s+", " ", d).strip()
            if not d:
                continue
            tab_counter[pfx] = tab_counter.get(pfx, 0) + 1
            rows.append({
                "tab": u.tab, "code": f"{pfx}-{tab_counter[pfx]:03d}",
                "name": u.title, "level": u.level_path, "detail": d,
            })
    return rows
