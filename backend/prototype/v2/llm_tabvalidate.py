"""
탭 검수 — 문서 **전체 탭을 한 번에** 보고 '실제 상세 요구사항 조견표 탭'만 keep.

탭을 하나씩 보면 요약/개요 탭('개발개념', '연구개발 개요')도 "기술 개발"이라 요구처럼
보여 못 거른다. 전체를 같이 보여줘야 "어느 게 상세본이고 어느 게 요약본/총론/비요구인지"
구조로 판단할 수 있다. 그래서 holistic(단일 호출)로 판정한다.

role:
  · detail_requirement(keep): 제안사가 무엇을 해야 하는지 요구('~해야 함/구축/구현/준수/제시/마련').
    '방안 제시·준수 방안'도 제안사 요구이므로 keep. 항목 수가 적어도 고유 요구면 keep.
  · summary_of_others(false): 다른 탭에 동일 항목/기술이 더 상세히 중복되어, 이 탭은 한 줄 요약·나열만.
    (반드시 '내용 중복'이 있어야 함. 짧다고 요약 아님.)
  · overview/background/status/scope/guide/toc/process(false): 개요·배경·현황·범위·안내·목차·절차.

안전장치: 가장 큰 탭(주 요구사항) 보존 + drop_cap(40%) 초과 차단 + LLM 실패 시 전체 보존.
gold 검증: 법제처(SFR/DAR…) 0 제거 / 하나 프로젝트범위·영역별Task 제거, 요구탭 보존 /
국방 개발개념·개요·필요성·과제제안요청서 제거.
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
    tab: str
    role: str
    keep: bool
    reason: str


class _Result(BaseModel):
    verdicts: list[_Verdict]


def _rows(items: list[Req], cap: int = 30) -> list[str]:
    """탭 항목 텍스트(항목명/요구사항 :: 상세, 200자). 큰 탭은 고르게 표본."""
    n = len(items)
    idxs = list(range(n)) if n <= cap else sorted(
        {round(i * (n - 1) / (cap - 1)) for i in range(cap)})
    out = []
    for i in idxs:
        top = (items[i].top or "").strip()
        mid = (items[i].mid or "").strip()
        det = (items[i].detail or "").strip()
        head = " / ".join(x for x in (top, mid) if x and x not in det)
        out.append((f"{head} :: {det}" if head else det)[:200])
    return out


def _prompt(blocks: list[tuple[str, int, list[str]]]) -> str:
    secs = []
    for t, n, rows in blocks:
        body = "\n".join(f"    {i + 1}. {r}" for i, r in enumerate(rows))
        secs.append(f"### 탭 '{t}' ({n}건)\n{body}")
    listing = "\n\n".join(secs)
    return (
        "한 RFP 문서의 모든 '탭'과 각 탭 항목(전체)이다. "
        "**실제 상세 요구사항 조견표 탭만 keep**, 나머지는 false.\n\n"
        "role 분류:\n"
        "- detail_requirement(keep): 제안사가 무엇을 해야 하는지의 요구. '~해야 함/구축/구현/준수/제시/마련' 형태.\n"
        "  예: 'X 기능을 구현하여야 함', '표준 아키텍처를 준수하고 그 준수 방안을 제시하여야 함',\n"
        "  '유지보수/확장 방안을 마련하여야 함'. ※ '방안을 제시/마련/수립'도 제안사 요구이므로 keep.\n"
        "  ※ 항목 수가 적어도(7건 등) 고유 요구면 keep.\n"
        "- summary_of_others(false): **다른 탭에 동일한 항목/기술이 더 상세히 중복**되어, 이 탭은\n"
        "  그것을 한 줄씩 요약·나열만 하는 경우에만. (예: '개발개념'이 다른 상세 탭들의 기술명을 한 줄씩 열거.)\n"
        "  반드시 '다른 탭과 내용 중복'이 있어야 함. **단지 짧거나 적다고 요약 아님. 고유 내용이면 keep.**\n"
        "- overview/background(false): 사업 개요·목표·배경·필요성·개념 총론('233억 투자하여 ~ 목표로 함').\n"
        "- status(false): 발주사 보유 현황·사양 나열. scope/toc/process/guide(false): 범위·목차·일정·안내.\n\n"
        "먼저 detail_requirement 탭들을 정하고, 그것들과 **내용이 겹치는 요약본**만 summary로 빼라.\n\n"
        f"{listing}\n\n"
        'JSON: {"verdicts":[{"tab":"<탭명 그대로>","role":"<위 역할>","keep":<bool>,"reason":"한 문장"}, ...]}'
        " — 모든 탭 빠짐없이."
    )


async def validate_tabs(reqs: list[Req], protected: set[str] | None = None,
                        drop_cap: float = 0.4) -> list[Req]:
    """문서 전체 탭을 한 번에 LLM 판정 → 비요구/요약 탭 제거."""
    by_tab: "OrderedDict[str, list[Req]]" = OrderedDict()
    for r in reqs:
        by_tab.setdefault(r.tab, []).append(r)
    if len(by_tab) < 2:
        return reqs

    blocks = [(t, len(items), _rows(items)) for t, items in by_tab.items()]
    s = Settings()
    client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)
    try:
        out = await client.structured_output(
            [Message(role="user", content=_prompt(blocks))], _Result,
            purpose="tab_validate", max_tokens=2500)
    except Exception:
        return reqs  # 실패 시 전체 보존

    drop = {v.tab for v in out.verdicts if not v.keep and v.tab in by_tab}
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
