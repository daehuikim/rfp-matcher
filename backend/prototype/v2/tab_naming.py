"""
문서 섹션 경로 → 사람이 읽기 좋은 탭 이름.

PDF RFP 는 leaf heading(1.1 구축방향, 4.2 정보보…)만 쓰면 탭이 과분할된다.
정보보호 4.x 는 4. 정보보호 요청사항 으로 묶고, ICT 2.4/2.5 는 각각 유지한다.
"""
from __future__ import annotations

import re

from .text import norm

_TAB_NUM = re.compile(
    r"^\s*(?:[IVXLCDM]+[.)]|[가나다라마바사아자차카타파하][.)]|[Ⅰ-Ⅻ]\.?|\d+(?:\.\d+)*\.?)\s+"
)
_TAB_BULLET = re.compile(r"^\s*[❍○●▪∙•◦·\-–—]\s*")
_TAB_BRACKET = re.compile(r"\[[^\]]*\]|\([^)]*\)|「[^」]*」")
_TRAIL = re.compile(r"\s*(?:관련\s*)?(?:요구사항|요청사항|요건|사항)\s*$")

# N.M 하위를 N. 부모 탭으로 묶을 때 — 부모 제목에 포함되면 merge (정보보호·AI 거버넌스 등)
_MERGE_PARENT_KW = ("정보보호", "거버넌스")
# N.M 을 leaf 탭으로 유지 — 부모가 ICT 등 포괄 카테고리일 때
_LEAF_UNDER_KW = ("ICT",)
# leaf 가 scope/범위(조견표 밖)일 때 — validate_tabs 가 제거 후보
_SCOPE_KW = ("제안 요청 범위", "사업 범위", "구축방향", "BMT", "실증")

_BAD_TAB = re.compile(
    r"(바랍니다|참고하시|다음의|아래와|오류!|책갈피)"
)
_NUM_ONLY = re.compile(r"^\d+$")
_NUM_LABEL = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*[.)]?|[가-하][.)]|[①-⑩⓪])\s*"
)


def _strip_num(seg: str) -> str:
    seg = norm(seg)
    for _ in range(4):
        n = _TAB_BULLET.sub("", _TAB_NUM.sub("", seg))
        if n == seg:
            break
        seg = n
    return seg.strip()


def clean_tab_segment(seg: str, *, max_len: int = 40) -> str:
    """번호·괄호·목차 leader 제거 후 탭 후보."""
    seg = _strip_num(seg)
    seg = _TAB_BRACKET.sub("", seg)
    seg = re.sub(r"[·.…‧\.\-_\s]{3,}\d*\s*$", "", seg)
    seg = re.sub(r"\s+\d+\s*$", "", seg).strip()
    return (seg or "요구사항")[:max_len]


def is_bad_tab_name(name: str) -> bool:
    """문장형 안내·TOC 오류 등 탭명으로 부적합."""
    if not name or name == "요구사항":
        return True
    if len(name) > 38:
        return True
    if _BAD_TAB.search(name):
        return True
    # 본문 문장이 섹션 제목으로 잘못 잡힌 경우 (JB '프로젝트 목적 본 사업은…')
    if re.search(r"본\s*사업|다음과\s*같|목적\s*및|추진\s*배경", name):
        return True
    if len(name) > 26 and not re.search(
        r"(요구사항|요청사항|요건|방안|범위|개요|표준|관리|지원|교육)$", name
    ):
        return True
    if re.search(r"(요구|요청|요건|방안|인프라|보안|UX|UI|데이터|AI|프로젝트)", name, re.I):
        return False
    return len(name) > 22


def _is_req_segment(seg: str) -> bool:
    s = clean_tab_segment(seg, max_len=200)
    return bool(re.search(r"(요구사항|요청사항|요건|기술요건)$", s))


def _parse_section_num(seg: str) -> tuple[str, str | None] | None:
    """'4.2. 제목' → ('4','2'), '4. 제목' → ('4', None). 번호 strip 전 원문 기준."""
    m = re.match(r"\s*(\d+)\.(\d+)\.\s", seg)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"\s*(\d+)\.\s", seg)
    if m:
        return m.group(1), None
    return None


def _major_num(seg: str) -> str | None:
    p = _parse_section_num(seg)
    return p[0] if p else None


def _sub_num(seg: str) -> tuple[str, str] | None:
    p = _parse_section_num(seg)
    if p and p[1] is not None:
        return p[0], p[1]
    return None


def coarse_tab_from_section(section_path: str) -> str:
    """section_path → 조견표 시트(탭) 이름."""
    parts = [p.strip() for p in (section_path or "").split(" > ") if p.strip()]
    if not parts:
        return "요구사항"

    # 번호 있는 세그먼트 (인덱스, 원문, major, sub)
    numbered: list[tuple[int, str, str | None, str | None]] = []
    for i, p in enumerate(parts):
        sn = _sub_num(p)
        if sn:
            numbered.append((i, p, sn[0], sn[1]))
        else:
            mn = _major_num(p)
            if mn:
                numbered.append((i, p, mn, None))

    if numbered:
        idx, part, major, sub = numbered[-1]
        if sub:
            for j in range(idx - 1, -1, -1):
                pj = parts[j]
                pn = _parse_section_num(pj)
                if not pn or pn[0] != major or pn[1] is not None:
                    continue
                parent_clean = clean_tab_segment(pj)
                if any(k in pj for k in _MERGE_PARENT_KW):
                    return parent_clean
                if any(k in pj for k in _LEAF_UNDER_KW):
                    return clean_tab_segment(part)
                if any(k in pj for k in _SCOPE_KW):
                    return clean_tab_segment(part)
                if _is_req_segment(pj) and not any(k in pj for k in _LEAF_UNDER_KW):
                    return parent_clean
        name = clean_tab_segment(part)
        if not is_bad_tab_name(name):
            return name
        short = _sentence_tab_fallback(name)
        if short:
            return short

    # leaf 가 문장형 안내 등이면 상위 세그먼트로 후퇴
    for seg in reversed(parts):
        name = clean_tab_segment(seg)
        if not is_bad_tab_name(name):
            return name
        short = _sentence_tab_fallback(name)
        if short:
            return short

    req_segs = [p for p in parts if _is_req_segment(p)]
    if req_segs:
        return clean_tab_segment(req_segs[-1])

    last = clean_tab_segment(parts[-1])
    return _sentence_tab_fallback(last) or (last if not is_bad_tab_name(last) else "요구사항")


def _sentence_tab_fallback(name: str) -> str:
    """'프로젝트 목적 본 사업은…' 같은 문장형 heading → 짧은 탭명."""
    if not name or not is_bad_tab_name(name):
        return ""
    m = re.match(r"^(.*?)\s*본\s*사업", name)
    if m:
        cand = m.group(1).strip()
        if len(cand) >= 4 and not is_bad_tab_name(cand):
            return cand
    if re.match(r"^프로젝트\s*목적", name):
        return "프로젝트 개요"
    return ""


def sanitize_hierarchy_label(text: str) -> str:
    """순번만 있는 항목명/요구사항 라벨 제거."""
    t = norm(text)
    if not t:
        return ""
    if _NUM_ONLY.match(t):
        return ""
    if len(t) <= 2 and _NUM_LABEL.match(t):
        return ""
    if _NUM_LABEL.match(t) and len(_NUM_LABEL.sub("", t).strip()) < 4:
        return ""
    return t


def sanitize_hierarchy_labels(reqs: list) -> list:
    """top/mid 에 '1', '2)', '가.' 같은 무의미 라벨 제거 (carry/LLM 후)."""
    for r in reqs:
        r.top = sanitize_hierarchy_label(r.top)
        r.mid = sanitize_hierarchy_label(r.mid)
    return reqs
