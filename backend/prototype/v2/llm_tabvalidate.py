"""
탭 검수 — 빌드 후 각 탭이 '진짜 요구사항 조견표 탭'인지 LLM 이 판정해 비요구 탭 제거.

예: '프로젝트 범위', '상세 요구사항은 다음을 참조하기 바랍니다', '일반 현황',
'제안서 작성 유의사항' 같은 안내/범위설명/절차 탭은 조견표 가치가 없으므로 drop.
탭 이름 + 대표 상세 샘플을 보고 keep/drop 만 판정(내용은 안 건드림).
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
    keep: bool
    reason: str


class _Result(BaseModel):
    verdicts: list[_Verdict]


def _prompt(samples: list[tuple[str, int, list[str]]]) -> str:
    blocks = []
    for tab, n, dets in samples:
        ex = " / ".join(d[:70] for d in dets)
        blocks.append(f"- 탭 '{tab}' ({n}건): {ex}")
    body = "\n".join(blocks)
    return (
        "RFP 요구사항 조견표의 탭 목록이다. 각 탭이 **제안사가 구축·이행할 시스템의 "
        "기능·기술 요구사항**을 담은 진짜 조견표 탭인지 판정하라(keep).\n"
        "keep=false(제거): 안내문(예: '다음을 참조하기 바랍니다'), 사업 개요/배경/범위 설명, "
        "발주사 일반/현황, 제안서 작성요령·제출·평가·유의사항, 목차, 연락처, 일정·가격 등 "
        "**요구사항 명세가 아닌 탭**.\n"
        "keep=true: 기능/데이터/보안/인프라/성능/UX/AI 등 실제 요구사항 명세 탭.\n\n"
        f"{body}\n\n"
        '응답 JSON: {"verdicts": [{"tab": "...", "keep": <bool>, "reason": "..."}, ...]} — 모든 탭.'
    )


async def validate_tabs(reqs: list[Req], protected: set[str] | None = None,
                        drop_cap: float = 0.4) -> list[Req]:
    """비요구 탭 제거. 안전장치: protected(폼 SFR 등) 제외 + 전체의 drop_cap(40%) 초과 제거는 차단."""
    protected = protected or set()
    by_tab: "OrderedDict[str, list[Req]]" = OrderedDict()
    for r in reqs:
        by_tab.setdefault(r.tab, []).append(r)
    if len(by_tab) < 2:
        return reqs
    cand = {t: items for t, items in by_tab.items() if t not in protected}
    if not cand:
        return reqs
    # 탭당 샘플 5개(앞·중·뒤 고루) — 큰 탭도 신뢰성 있게 판정
    samples = []
    for t, items in cand.items():
        idx = sorted({0, len(items) // 2, len(items) - 1, len(items) // 4, 3 * len(items) // 4})
        samples.append((t, len(items), [items[i].detail for i in idx if i < len(items)]))
    s = Settings()
    client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)
    try:
        out = await client.structured_output(
            [Message(role="user", content=_prompt(samples))], _Result,
            purpose="tab_validate", max_tokens=2500)
    except Exception:
        return reqs
    drop = {v.tab for v in out.verdicts if not v.keep and v.tab in cand}
    if not drop:
        return reqs
    drop_rows = sum(len(by_tab[t]) for t in drop)
    if drop_rows > len(reqs) * drop_cap:   # 과도한 제거(예: 폼 전체) 차단
        return reqs
    return [r for r in reqs if r.tab not in drop]


def validate_tabs_sync(reqs: list[Req], protected: set[str] | None = None) -> list[Req]:
    return asyncio.run(validate_tabs(reqs, protected))
