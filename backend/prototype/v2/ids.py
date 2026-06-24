"""요구사항 ID 생성 — 도메인 주제 접두사 + 통합문서 일련번호."""
from __future__ import annotations

import re

from .extract import Req
from .tab_naming import sanitize_hierarchy_label

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")
_BAD_PREFIX = re.compile(r"^\d+$")

# domain top → ID 접두사 (정답 조견표·LLM coarsen 과 공유)
_PREFIX_ALIASES: list[tuple[str, str]] = [
    (r"지식베이스|RAG", "지식베이스"),
    (r"비정형\s*데이터", "비정형데이터"),
    (r"프로젝트\s*범위", "프로젝트범위"),
    (r"프로젝트\s*수행", "프로젝트수행"),
    (r"프로젝트\s*관리", "프로젝트관리"),
    (r"리스크", "리스크"),
    (r"기술지원", "기술지원"),
    (r"Agent.?플랫폼|AI Agent|AI Studio|Tool.?Repo", "Agent플랫폼"),
    (r"운영.?환경|지속가능", "운영환경"),
    (r"운영자동화|ICT운영", "운영자동화Agent"),
    (r"시스템.?일반|^시스템$", "시스템"),
    (r"정보처리", "정보처리시스템"),
    (r"개인정보", "개인정보처리시스템"),
    (r"AI.?플랫폼.?보안|AI활용", "AI플랫폼보안"),
    (r"연계.?시스템", "연계시스템"),
    (r"인프라.?DB|^DB$", "인프라DB"),
    (r"인프라.?아키텍처|아키텍처", "인프라아키텍처"),
    (r"인프라.?공통|인프라.?인력", "인프라공통"),
    (r"UX|UI", "UXUI"),
]


def _slug(text: str) -> str:
    m = _TOKEN.search(text or "")
    return m.group(0) if m else ""


def _good_prefix(s: str) -> bool:
    s = sanitize_hierarchy_label(s)
    if not s or len(s) < 2:
        return False
    slug = _slug(s)
    return bool(slug) and not _BAD_PREFIX.match(slug) and len(slug) >= 2


def domain_slug(domain: str) -> str:
    """도메인 항목명 → 요구사항 ID 접두사."""
    d = sanitize_hierarchy_label(domain or "")
    if not d:
        return "요구사항"
    for pat, slug in _PREFIX_ALIASES:
        if re.search(pat, d, re.I):
            return slug
    slug = _slug(d)
    if slug and len(slug) >= 2 and not _BAD_PREFIX.match(slug):
        return slug[:24]
    return "요구사항"


def id_prefix(r: Req) -> str:
    """top → tab 순 slug. mid 단독(연속행 조각)은 ID 접두사로 쓰지 않음."""
    for val in (r.top, r.tab):
        v = sanitize_hierarchy_label(val or "")
        if _good_prefix(v):
            return domain_slug(v)
    mid = sanitize_hierarchy_label(r.mid or "")
    if mid and len(mid) >= 4 and _good_prefix(mid):
        return domain_slug(mid)
    return "요구사항"


def assign_ids(reqs: list[Req]) -> list[Req]:
    """**탭 기반** ID — 한 탭 = 한 접두사(탭명 slug), 탭별 일련번호. {prefix}_{NNN}.

    한 시트 안에서 ID 접두사가 섞이면 안 된다는 요구 — 접두사를 항목명(top)이 아니라 **탭**에서 뽑는다.
    탭이 곧 도메인이므로 시트 내 모든 행이 동일 접두사 + 연속 번호를 갖는다(부록은 표 단위).
    """
    counters: dict[str, int] = {}     # 접두사별 전역 카운터(중복 ID 방지)
    tab_prefix: dict[str, str] = {}    # 탭 → 접두사(슬러그 충돌 시 탭별 유일화)
    used_prefixes: set[str] = set()
    appendix_by_table: dict[int, str] = {}
    appendix_n = 0

    for r in reqs:
        if r.rid:
            continue
        if (r.tab or "").strip() == "부록":
            tid = r.table_id if r.table_id is not None else -1
            if tid not in appendix_by_table:
                appendix_n += 1
                appendix_by_table[tid] = f"부록_{appendix_n:03d}"
            r.rid = appendix_by_table[tid]
            r.gen_rid = True
            continue

        tab = (r.tab or "요구사항").strip()
        if tab not in tab_prefix:  # 탭마다 접두사 1개 확정(다른 탭과 슬러그 충돌 시 _2 등으로 유일화)
            base = domain_slug(tab)
            pfx, k = base, 2
            while pfx in used_prefixes:
                pfx = f"{base}{k}"
                k += 1
            used_prefixes.add(pfx)
            tab_prefix[tab] = pfx
        pfx = tab_prefix[tab]
        counters[pfx] = counters.get(pfx, 0) + 1
        r.rid = f"{pfx}_{counters[pfx]:03d}"
        r.gen_rid = True
    return reqs
