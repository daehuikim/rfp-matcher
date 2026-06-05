"""요구사항 ID 생성 — 항목명(top) 도메인 접두사 + 일련번호. (탭, 항목명) 단위 연속."""
from __future__ import annotations

import re

from .extract import Req

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")


def _slug(top: str) -> str:
    m = _TOKEN.search(top or "")
    return m.group(0) if m else "요구사항"


def assign_ids(reqs: list[Req]) -> list[Req]:
    """같은 탭 = 같은 접두사 + 일련번호 (탭 내 ID 일관성)."""
    counters: dict[str, int] = {}
    prefix: dict[str, str] = {}
    for r in reqs:
        if r.rid:  # 이미 ID 있음(세로형 표의 고유번호 SFR-001 등) → 보존
            continue
        tab = r.tab or "요구사항"
        if tab not in prefix:
            prefix[tab] = _slug(tab)
        counters[tab] = counters.get(tab, 0) + 1
        r.rid = f"{prefix[tab]}-{counters[tab]:03d}"
        r.gen_rid = True   # 생성된 ID(원문 SFR-001 등은 보존되어 여기 안 옴)
    return reqs
