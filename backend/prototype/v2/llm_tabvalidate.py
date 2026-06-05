"""
탭 검수 — 각 탭이 '요구사항 조견표 탭'인지 LLM 이 **내용 기반**으로 판정해 비요구 탭 제거.

핵심 판단: 각 항목이 *제안사(수주사)가 새로 구축·제공·준수·제시·이행해야 하는 요구*인가,
아니면 *발주사가 이미 보유/운영 중인 현황·환경·사양을 서술*하거나 *개요/배경/목차/절차*인가.
탭 이름이 안내문처럼 보여도 내용이 요구면 keep(이름 아니라 내용으로).

탭별로 독립 판정(LLM 병렬). proposer_req_ratio(제안사 요구 항목 비율) < 0.5 면 제거.
안전장치: 가장 큰 탭(주 요구사항)은 절대 제거하지 않고, drop_cap(40%) 초과 차단.
LLM 실패/오류 시 보존(over-drop 방지).
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.openai_client import OpenAIClient

from .extract import Req


class _Verdict(BaseModel):
    proposer_req_ratio: float  # 제안사가 구축/제공/준수/제시해야 하는 항목 비율(0~1)
    kind: str                  # requirement|status|overview|scope|guide|toc|process
    keep: bool
    reason: str


def _tab_rows(items: list[Req], full_max: int = 40, big: int = 40) -> list[str]:
    """탭의 행 텍스트(항목명/요구사항 :: 상세). 작은 탭은 전부, 큰 탭은 고르게 표본."""
    n = len(items)
    idxs = list(range(n)) if n <= full_max else sorted(
        {round(i * (n - 1) / (big - 1)) for i in range(big)})
    out = []
    for i in idxs:
        top = (items[i].top or "").strip()
        mid = (items[i].mid or "").strip()
        det = (items[i].detail or "").strip()
        head = " / ".join(x for x in (top, mid) if x and x not in det)
        out.append((f"{head} :: {det}" if head else det)[:140])
    return out


def _prompt(tab: str, n: int, rows: list[str]) -> str:
    body = "\n".join(f"  {i + 1}. {r}" for i, r in enumerate(rows))
    return (
        "한 RFP 문서에서 추출한 '탭(시트)'의 항목들이다. 이 탭이 '요구사항 조견표 탭'인지 판정하라.\n\n"
        "가장 중요한 구분: 각 항목이 **제안사(수주사)가 새로 구축·제공·준수·제시·이행해야 하는 '요구'**인가,\n"
        "아니면 **발주사가 이미 보유/운영 중인 현황·환경·사양을 '서술'한 것**인가?\n"
        "- 발주사의 기존 인프라/조직/시스템 현황·사양 나열(예: 'CPU 64Core', '컨테이너 런타임 containerd',\n"
        "  '환경 구분: 개발/운영')은 제안사가 만드는 게 아니므로 **요구 아님(status)**. 기술 용어가 많아도 false.\n"
        "- 사업 개요·배경·목적·필요성·추진방향·개발개념은 큰 틀의 총론이면 **요구 아님(overview)**.\n"
        "- 범위 설명(scope)·목차(toc)·추진체계/일정/예산(process)·안내참조/작성요령/평가(guide) = 요구 아님.\n"
        "- '~해야 한다/제공/구축/지원/연계/준수/제시하여야' 등 제안사에게 행위를 요구하면 요구(requirement).\n"
        "- 탭 이름이 안내문 같아도 내용이 요구면 keep.\n\n"
        "proposer_req_ratio = 위 정의의 '제안사 요구' 항목 비율(0~1). keep = (ratio >= 0.5).\n\n"
        f"## 탭 '{tab}' (총 {n}건)\n{body}\n\n"
        'JSON: {"proposer_req_ratio":<0~1>,"kind":"requirement|status|overview|scope|guide|toc|process",'
        '"keep":<bool>,"reason":"한 문장"}'
    )


async def _judge(client, tab: str, items: list[Req]) -> tuple[str, bool]:
    try:
        out = await client.structured_output(
            [Message(role="user", content=_prompt(tab, len(items), _tab_rows(items)))],
            _Verdict, purpose="tab_validate", max_tokens=400)
        return tab, bool(out.keep and out.proposer_req_ratio >= 0.5)
    except Exception:
        return tab, True  # 실패 시 보존


async def validate_tabs(reqs: list[Req], protected: set[str] | None = None,
                        drop_cap: float = 0.4) -> list[Req]:
    """탭별 내용 기반 LLM 판정으로 비요구 탭 제거."""
    by_tab: "OrderedDict[str, list[Req]]" = OrderedDict()
    for r in reqs:
        by_tab.setdefault(r.tab, []).append(r)
    if len(by_tab) < 2:
        return reqs

    s = Settings()
    client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)
    sem = asyncio.Semaphore(max(2, s.llm_concurrency))

    async def _guarded(t, items):
        async with sem:
            return await _judge(client, t, items)

    results = await asyncio.gather(*[_guarded(t, items) for t, items in by_tab.items()])
    drop = {t for t, keep in results if not keep}

    # 안전장치: 가장 큰 탭(주 요구사항)은 절대 제거하지 않음
    largest = max(by_tab, key=lambda t: len(by_tab[t]))
    drop.discard(largest)
    if not drop:
        return reqs
    drop_rows = sum(len(by_tab[t]) for t in drop)
    if drop_rows > len(reqs) * drop_cap:   # 과도한 제거 차단
        return reqs
    return [r for r in reqs if r.tab not in drop]


def validate_tabs_sync(reqs: list[Req], protected: set[str] | None = None) -> list[Req]:
    return asyncio.run(validate_tabs(reqs, protected))
