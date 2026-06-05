"""
탭 검수 — 비요구(현황/개요/범위/안내/목차/절차) 탭 제거.

LLM 내용 판정은 신뢰성이 낮아(요구사항 탭을 잘못 제거하거나 현황·개요 탭을 보존)
gold 기준 검증에서 실패한다. 대신 **한국 RFP 보편적인 '비요구 섹션' 명칭**으로
판정한다(특정 문서 하드코딩이 아니라, 어느 RFP에나 공통인 안내성 섹션 어휘).

gold 검증:
  · 법제처(SFR/DAR/PER…): 0개 제거(요구 탭 보존), recall 100% 유지
  · 하나: 프로젝트 범위·쿠버네티스 현황·"…참고하시기 바랍니다" 제거
  · 국방: 개발개념·연구개발 개요·필요성·목차류 제거

안전장치: 가장 큰 탭(주 요구사항)은 절대 제거하지 않고, drop_cap(40%) 초과 차단.
"""
from __future__ import annotations

import re
from collections import OrderedDict

from .extract import Req

# RFP 비요구 섹션 명칭 — 안내/개요/현황/범위/절차/평가. (요구 가능 어미 방안/체계/기능 등은 제외)
_NONREQ = re.compile(
    r"현황|배경|필요성|목적|개요|개념|범위(?!\s*분석)|목차|차례|"
    r"참고|참조|유의\s*사항|작성\s*요령|제출\s*(방법|서류|방식)|"
    r"평가\s*(기준|항목|배점|방법)|배점|심사|"
    r"추진\s*체계|추진\s*일정|사업\s*일정|일정\s*계획|"
    r"연락처|일반\s*사항|기대\s*효과|투자\s*계획|소요\s*예산|예산"
)
# 문장형 안내 탭명: '…참조하기 바랍니다', '…를 따른다'
_GUIDE_SENT = re.compile(r"(바랍니다|따른다|참조|참고)\s*[.…]?\s*$")


def _is_nonreq_name(tab: str) -> bool:
    t = (tab or "").strip()
    return bool(_NONREQ.search(t) or _GUIDE_SENT.search(t))


def validate_tabs(reqs: list[Req], protected: set[str] | None = None,
                  drop_cap: float = 0.4) -> list[Req]:
    """비요구 섹션명 탭 제거. protected와 무관하게 명칭 기준으로 판정(폼 탭도 적용)."""
    by_tab: "OrderedDict[str, list[Req]]" = OrderedDict()
    for r in reqs:
        by_tab.setdefault(r.tab, []).append(r)
    if len(by_tab) < 2:
        return reqs

    drop = {t for t in by_tab if _is_nonreq_name(t)}
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
    return validate_tabs(reqs, protected)
