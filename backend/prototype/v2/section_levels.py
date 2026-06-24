"""문서 섹션 계층을 N계위로 승격 — 고정 칼럼/키워드 없음, 충실 전사.

시트(tab)별 공통 섹션 prefix '아래'(시트 내에서 갈라지는) 섹션을 계위 칸으로 올리고,
그 뒤 라벨러 top/mid를 더 깊은 계위로 붙인다. 섹션 번호(1.1/가./2.3.1)는 위치 식별을 위해
유지하고, 과깊이는 안쪽(구체적)부터 max_levels 개로 자른다.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .extract import Req

_NUMHEAD = re.compile(r"^\s*(?:[IVXLC]+|\d+(?:\.\d+)*|[가-힣]|[①-⑩])[.)]\s*")
MAX_LEVELS = 4


def _bare(s: str) -> str:
    """번호·머리표 제거한 비교용 텍스트."""
    return _NUMHEAD.sub("", (s or "")).strip()


def assign_section_levels(
    reqs: list[Req], max_levels: int = MAX_LEVELS, *, use_section_levels: bool = True
) -> list[Req]:
    """top/mid 를 계위 슬롯으로 부착. use_section_levels=True 면 시트 공통섹션 아래
    section_path 잔여를 바깥 계위로 승격(중첩 번호헤딩 문서). 평면 문서는 section_path 가
    구조정보가 아니므로 False 로 호출해 top/mid(항목명/요구사항)만 쓴다."""
    by_tab: dict[str, list[Req]] = defaultdict(list)
    for r in reqs:
        by_tab[r.tab or ""].append(r)
    for group in by_tab.values():
        seclists = [
            [p.strip() for p in (r.section_path or "").split(">") if p.strip()] for r in group
        ]
        common = 0
        if use_section_levels and seclists:
            mn = min(len(s) for s in seclists)
            while common < mn and len({_bare(s[common]) for s in seclists}) == 1:
                common += 1
        for r, sl in zip(group, seclists):
            if r.levels:  # 이미 슬롯보존 levels 가 채워진 행은 존중(상류가 직접 지정한 경우)
                continue
            sec_levels: list[str] = []
            if use_section_levels:
                for p in sl[common:]:  # 시트 공통 섹션 아래 = 시트 내 계위
                    pn = _bare(p)
                    if pn and (not sec_levels or _bare(sec_levels[-1]) != pn):
                        sec_levels.append(p)  # 번호 유지(위치 식별)
            # top/mid 는 **고정 슬롯 페어**로 부착 — 빈 top 필터링 금지(position 보존).
            # top='' + mid='X' → [...sec, '', 'X'] (요구사항 칸 유지, 항목명 빈칸=병합).
            top_s = (r.top or "").strip()
            mid_s = (r.mid or "").strip()
            if sec_levels and top_s:  # 항목명이 섹션 헤딩과 사실상 같으면 중복 → 빈칸(위치 유지)
                lastb, tb = _bare(sec_levels[-1]), _bare(top_s)
                if tb and (tb == lastb or tb in lastb or lastb in tb):
                    top_s = ""
            pair = [top_s, mid_s]
            while pair and not pair[-1]:  # 트레일링 빈칸만 제거(선두/중간 빈칸 보존)
                pair.pop()
            levels = sec_levels + pair
            if len(levels) > max_levels:  # 과깊이 방지 — 안쪽(구체적) 우선
                levels = levels[-max_levels:]
            r.levels = levels
    return reqs
