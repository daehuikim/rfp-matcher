"""
탭 검수 — 빌드 후 각 탭이 '진짜 요구사항 조견표 탭'인지 LLM 이 판정해 비요구 탭 제거.

예: '프로젝트 범위', '상세 요구사항은 다음을 참조하기 바랍니다', '일반 현황',
'제안서 작성 유의사항' 같은 안내/범위설명/절차 탭은 조견표 가치가 없으므로 drop.
탭 이름 + 대표 상세 샘플을 보고 keep/drop 만 판정(내용은 안 건드림).
"""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.openai_client import OpenAIClient

from .extract import Req

# 탭 이름만으로 비요구가 명백한 패턴 (현황/범위/개요/안내/목차/추진체계 등).
# '~방안/체계/기능' 등 요구가능한 어미는 제외하고, 명백한 비요구 명사구만.
_NONREQ_NAME = re.compile(
    r"(현황|배경|필요성|목적|개요|범위|목차|차례|참고|참조|유의\s*사항|작성\s*요령|"
    r"제출\s*(방법|서류)|평가\s*(기준|항목|배점)|배점|추진\s*체계|추진\s*일정|"
    r"사업\s*일정|개발\s*개념|연락처|일반\s*사항)"
)


def _name_nonreq(tab: str) -> bool:
    """탭 이름이 명백한 비요구(현황·범위·안내·목차…)면 True."""
    t = tab.strip()
    if _NONREQ_NAME.search(t):
        return True
    # '…참고/참조하시기 바랍니다', '…를 따른다' 류 안내 문장형 탭명
    if re.search(r"(바랍니다|따른다|참조|참고)\s*\.?$", t):
        return True
    return False


class _Verdict(BaseModel):
    tab: str
    keep: bool
    reason: str


class _Result(BaseModel):
    verdicts: list[_Verdict]


def _prompt(samples: list[tuple[str, int, list[str]]]) -> str:
    blocks = []
    for tab, n, dets in samples:
        lines = "\n".join(f"    · {d}" for d in dets)
        blocks.append(f"- 탭 '{tab}' (총 {n}건, 대표 {len(dets)}건):\n{lines}")
    body = "\n".join(blocks)
    return (
        "RFP 요구사항 조견표의 탭 목록이다. 각 탭이 **제안사가 구축·이행할 시스템의 "
        "기능·기술 요구사항**을 담은 진짜 조견표 탭인지 탭별로 판정하라(keep).\n"
        "탭 이름만 보지 말고, 아래 대표 항목들의 '내용 성격'을 충분히 읽고 판단하라.\n\n"
        "keep=false(제거):\n"
        "  · 안내/참조문('…는 다음을 참조하기 바랍니다', '상세는 별첨')\n"
        "  · 사업 개요·배경·목적·필요성·추진방향, **개발개념·추진체계·추진일정**\n"
        "  · 발주사 일반현황, 제안서 작성요령·제출·평가·배점·유의사항, **목차/차례**\n"
        "  · 연락처·일정·예산·가격 등 — **시스템 요구 명세가 아닌 것**\n"
        "keep=true: 기능/데이터/보안/인프라/성능/연계/UX/AI/운영 등 '~해야 한다/제공/구축/지원'류 실제 요구 명세.\n"
        "기준: 항목들이 *제안사가 만들/이행할 것*을 요구하면 keep, *발주사가 설명/안내*하면 false.\n\n"
        f"{body}\n\n"
        '응답 JSON: {"verdicts": [{"tab": "...", "keep": <bool>, "reason": "근거 한 문장"}, ...]} — 모든 탭 빠짐없이.'
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
    # 이름만으로 명백한 비요구 탭은 protected라도 제거 후보 (LLM 콜 전에 먼저 확정)
    name_drop = {t for t in by_tab if _name_nonreq(t)}
    cand = {t: items for t, items in by_tab.items() if t not in protected}
    # 탭당 대표 표본을 넉넉히(최대 12건, 작은 탭은 전부) 고르게 추출 — 검수 신뢰성↑
    def _pick(items: list[Req], k: int = 12) -> list[str]:
        n = len(items)
        if n <= k:
            idxs = list(range(n))
        else:
            idxs = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
        out = []
        for i in idxs:
            top = (items[i].top or "").strip()
            det = (items[i].detail or "").strip()
            txt = f"{top} — {det}" if top and top not in det else det
            out.append(txt[:200])
        return out

    llm_drop: set[str] = set()
    if cand:  # 검수 대상(비protected)이 있으면 LLM 내용 기반 판정
        samples = [(t, len(items), _pick(items)) for t, items in cand.items()]
        s = Settings()
        client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)
        try:
            out = await client.structured_output(
                [Message(role="user", content=_prompt(samples))], _Result,
                purpose="tab_validate", max_tokens=4000)
            llm_drop = {v.tab for v in out.verdicts if not v.keep and v.tab in cand}
        except Exception:
            llm_drop = set()

    drop = llm_drop | name_drop

    # 가장 큰 탭(주 요구사항)은 절대 제거하지 않음 — 오판 안전장치
    largest = max(by_tab, key=lambda t: len(by_tab[t]))
    drop.discard(largest)
    if not drop:
        return reqs
    drop_rows = sum(len(by_tab[t]) for t in drop)
    if drop_rows > len(reqs) * drop_cap:   # 과도한 제거(예: 폼 전체) 차단
        return reqs
    return [r for r in reqs if r.tab not in drop]


def validate_tabs_sync(reqs: list[Req], protected: set[str] | None = None) -> list[Req]:
    return asyncio.run(validate_tabs(reqs, protected))
