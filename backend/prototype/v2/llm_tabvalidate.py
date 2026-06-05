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
    kind: str   # requirement|overview|background|status|scope|guide|toc|process
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


# 정답(gold) 기반 few-shot — '무엇이 요구인지(형태)'를 예시로 고정.
_FEWSHOT = (
    "'요구사항'은 제안사(수주사)가 무엇을 구축·제공·구현·준수·제시해야 하는지 명시한 "
    "개별·검증가능한 진술이다.\n\n"
    "[요구사항 O — 이런 형태]\n"
    "  · 사용자의 자연어 질문에서 의도를 분석하고 질의를 재구성하는 기능을 구현하여야 함\n"
    "  · 고성능 벡터 DB 인프라를 구축하여 검색 성능을 고도화하여야 함\n"
    "  · DB 구조 설계 시 향후 업무 변동에 따른 확장성을 충분히 고려하여야 함\n"
    "  · 보안 규정·지침을 준수하고 보안약점 없이 개발하여야 함\n"
    "  → 공통: 제안사가 '~하여야 함/구현/구축/제공/준수/제시'. 기능·데이터·보안·인프라·성능·연계·운영.\n\n"
    "[요구사항 X — 항목 다수가 이런 형태이면 그 탭은 제거]\n"
    "  · 본 사업은 …를 구축하는 것을 목표로 한다 (overview: 개요)\n"
    "  · 추진배경: 기존 시스템의 한계로 … (background: 배경/필요성)\n"
    "  · 당행 K8S Worker: CPU 64Core, MEM 1024GB / 컨테이너 런타임 containerd (status: 발주사 보유 현황·사양)\n"
    "  · 본 사업 범위는 …를 포함한다 (scope: 범위 설명)\n"
    "  · 상세 요구사항은 다음을 참조하기 바랍니다 (guide: 안내)\n"
    "  · Ⅰ.사업개요 Ⅱ.사업내용 … (toc: 목차) / 추진일정·추진체계·예산 (process: 절차)\n\n"
    "판정: 이 탭의 항목 **다수**가 [요구사항 O] 형태이면 keep=true, [요구사항 X] 형태이면 false.\n"
    "탭 이름이 안내문 같아도 항목 내용이 요구 형태면 keep. 개발개념/개요/배경처럼 큰 틀 서술이면 false.\n\n"
)


def _prompt(tab: str, n: int, rows: list[str]) -> str:
    body = "\n".join(f"  {i + 1}. {r}" for i, r in enumerate(rows))
    return (
        _FEWSHOT
        + f"## 탭 '{tab}' (총 {n}건)\n{body}\n\n"
        + 'JSON: {"kind":"requirement|overview|background|status|scope|guide|toc|process",'
          '"keep":<bool>,"reason":"한 문장"}'
    )


async def _judge(client, tab: str, items: list[Req]) -> tuple[str, bool]:
    try:
        out = await client.structured_output(
            [Message(role="user", content=_prompt(tab, len(items), _tab_rows(items)))],
            _Verdict, purpose="tab_validate", max_tokens=300)
        return tab, bool(out.keep)
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
