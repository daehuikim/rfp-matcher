"""
금융 RFP heading 라벨 — 절 번호(2.7.4) 유지, 탭·요건구분·출처 힌트.

clean_heading / clean_tab_segment 는 번호를 제거하므로 금융 조견표 추적용으로 별도 유지.
"""
from __future__ import annotations

import re

from prototype.v2.text import norm

_TRAIL = re.compile(r"\s*(?:관련\s*)?(?:요구사항|요청사항|요건|사항)\s*$", re.I)
_BRACKET = re.compile(r"\[[^\]]*\]|「[^」]*」")
_NUM_SECTION = re.compile(r"^(\d+(?:\.\d+)*)\.\s*(.+)$")


def preserve_group_label(text: str) -> str:
    """요건구분·subgroup — '2.7.4. 유지보수 이행방안' 번호 유지."""
    t = norm(text)
    if not t:
        return ""
    t = _BRACKET.sub("", t).strip()
    if not re.search(r"기타\s*(?:요청|요구)사항", t, re.I):
        t = _TRAIL.sub("", t).strip()
    return t


_NARRATIVE_START = re.compile(r"^(?:본|상기|다음|제안|프로젝트|당행|귀사|위\s)", re.I)
_TITLE_BODY = re.compile(
    r"^(\d+(?:\.\d+)*\.\s+(?:기타\s*)?(?:요청|요구)사항)\s+(.+)$",
    re.I,
)
_TITLE_BODY_PLAIN = re.compile(r"^(기타\s*(?:요청|요구)사항)\s+(.+)$", re.I)
_TITLE_NARR = re.compile(
    r"^(\d+(?:\.\d+)*\.\s+.+?)\s+((?:본|상기|다음|제안|프로젝트|당행|귀사|위\s).+)$",
)


def split_title_body(text: str) -> tuple[str, str]:
    """한 줄에 섹션 제목 + 서술형 본문이 붙은 경우 분리."""
    t = norm(text)
    if not t:
        return "", ""
    for pat in (_TITLE_BODY, _TITLE_BODY_PLAIN, _TITLE_NARR):
        m = pat.match(t)
        if m and len(m.group(1)) <= 56 and _NARRATIVE_START.match(m.group(2).strip()):
            return norm(m.group(1)), norm(m.group(2))
    return "", ""


def category_path(section_path: str) -> str:
    """카테고리 칼럼 — 문서 heading 계층 (요건구분 라벨 제외)."""
    parts = [norm(p) for p in (section_path or "").split(" > ") if norm(p)]
    return " > ".join(parts)


_SEC_NUM = re.compile(r"^(\d+(?:\.\d+)*)\.")
_NARRATIVE_GROUP = re.compile(
    r"다음과\s*같|제안해|주시기|바랍니다|구성요소는|참고하|요청합니다|으며|하고,|수행하여|"
    r"제시하여|기술하여|포함하여|적용하여|확인하여|제공하여",
    re.I,
)
_BULLET = re.compile(r"^[•·▪◦∙\-–—]")


_SUBGROUP_KO = re.compile(r"^[가-하]\.\s+.+")
_NUMBERED_DETAIL = re.compile(r"^\d+\)\s*")


def is_numbered_detail_item(text: str) -> bool:
    """1) 2) … — 상세내용 행. 요건구분 아님."""
    return bool(_NUMBERED_DETAIL.match(norm(text)))


def is_subgroup_label(text: str) -> bool:
    """가. 지식베이스 구축(RAG) — 요건구분 소제목."""
    t = norm(text)
    if not _SUBGROUP_KO.match(t):
        return False
    if len(t) > 44:
        return False
    if _NARRATIVE_GROUP.search(t):
        return False
    if re.search(r"(을|를|하여|위한|통한)\s", t) and len(t) > 28:
        return False
    return True


def lowest_category_label(section_path: str) -> str:
    """요건구분 폴백 — 카테고리 경로 최하위 절 제목 (2.3.1, (3) 클러스터 현황 등)."""
    parts = [preserve_group_label(p) for p in (section_path or "").split(" > ") if norm(p)]
    parts = [p for p in parts if p]
    for part in reversed(parts):
        if is_valid_req_group_label(part):
            return part
        if re.match(r"^\d+(?:\.\d+)*\.\s+", part) and len(part) <= 52:
            return part
        if re.match(r"^\(\d+\)\s+", part) and len(part) <= 48:
            return part
    return ""


def is_valid_req_group_label(text: str) -> bool:
    """요건구분 칼럼 — 짧은 항목명만. 서술형·번호목록은 상세내용."""
    t = norm(text)
    if not t or _BULLET.match(t):
        return False
    if is_numbered_detail_item(t):
        return False
    if is_subgroup_label(t):
        return True
    if len(t) > 52:
        return False
    if _NARRATIVE_GROUP.search(t):
        return False
    if re.search(r"(해야|합니다|니다|하여야|제시|기술)\s*\.?\s*$", t):
        return False
    if re.match(r"^\d+(?:\.\d+)*\.\s+", t) and len(t) <= 40:
        return True
    if len(t) > 32:
        return False
    return bool(re.search(r"[가-힣]", t))


def tab_from_section_path(section_path: str, llm_tab: str = "") -> str:
    """Excel 탭 — N.N 절(1.4, 2.3) 단위로 묶어 1.4 / 1.4.3 분리 방지."""
    parts = [norm(p) for p in (section_path or "").split(" > ") if norm(p)]
    tab = ""
    for part in parts:
        m = _SEC_NUM.match(part)
        if m and m.group(1).count(".") == 1:
            tab = preserve_tab_name(part)
    if tab:
        return tab
    if llm_tab:
        return preserve_tab_name(llm_tab)
    for part in reversed(parts):
        if re.match(r"^\d+(?:\.\d+)*\.", part):
            return preserve_tab_name(part)
    return preserve_tab_name(parts[-1]) if parts else "요구사항"


def preserve_tab_name(text: str, *, max_len: int = 31) -> str:
    """Excel 탭명 — 절 번호 유지, 길이만 제한."""
    t = preserve_group_label(text)
    if not t:
        return "요구사항"
    return t[:max_len]


def section_depth(num: str) -> int:
    """'2.7.4' → outline depth (h-level 추정용)."""
    return num.count(".") + 1


def heading_level_from_number(num: str) -> int:
    """번호 깊이 → h3~h6 (문서 outline 구조)."""
    d = section_depth(num)
    return min(6, max(3, 2 + d))


def parse_numbered_section(text: str) -> tuple[str, str] | None:
    """'2.7.4. 유지보수 이행방안' → ('2.7.4', '유지보수 이행방안')."""
    t = norm(text)
    m = _NUM_SECTION.match(t)
    if not m:
        return None
    return m.group(1), norm(m.group(2))


def is_numbered_section_title(text: str) -> bool:
    """`<p>`/`<li>` 한 줄이 절 제목인지 (본문 문장과 구분)."""
    parsed = parse_numbered_section(text)
    if not parsed:
        return False
    _num, body = parsed
    if len(body) > 72:
        return False
    if re.search(r"(다음과\s*같|참고하|요청하며|제시합|하여야\s*합니다)", body):
        return False
    return True


def section_source_hint(section_path: str, *, segments: int = 2) -> str:
    """출처 칼럼용 계위 힌트 — 최근 N단계, 번호 유지."""
    parts = [preserve_group_label(p) for p in (section_path or "").split(" > ") if norm(p)]
    parts = [p for p in parts if p and not p.startswith("표")]
    if not parts:
        return ""
    tail = parts[-segments:]
    return " > ".join(tail)
