"""텍스트 정규화 유틸 (도메인 지식 없음, 순수 구조)."""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_NONWORD = re.compile(r"[^0-9A-Za-z가-힣]+")
_CODEISH = re.compile(r"^[A-Za-z0-9가-힣]{0,6}[_\-/.]?\d{1,4}$")


# PDF 심볼폰트 변환 시 깨지는 글자 보정 (à 0xE0 = 화살표 →)
_BROKEN = {"à": "→", "": "→", "": "→"}


def norm(s) -> str:
    t = str(s or "")
    for bad, good in _BROKEN.items():
        if bad in t:
            t = t.replace(bad, good)
    return _WS.sub(" ", t.strip())


def sig(s) -> str:
    """매칭용 시그니처: 공백·기호 제거."""
    return _NONWORD.sub("", str(s or ""))


def looks_like_code(s: str) -> bool:
    """짧은 ID/코드형(예: 시스템_001, No.3, SFR-12) 여부 — 형상 신호."""
    s = norm(s)
    return bool(s) and (len(s) <= 12 and bool(_CODEISH.match(s)))
