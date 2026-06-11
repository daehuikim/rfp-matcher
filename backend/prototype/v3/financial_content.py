"""
금융 RFP 본문 판정 — 섹션 번호·도메인 키워드 없이 맥락+내용으로 요구사항 여부 결정.

사람이 읽을 때와 같이:
  - 제목 맥락: 요청/방안/요구 vs 당행·H/W·클러스터 현황
  - 본문 신호: ~해야 함, 제시, 구축 vs 장비 사양 나열
  - 표 형태: 스키마 기반 요구표 vs 인벤토리 표
"""
from __future__ import annotations

import re

from prototype.v2.grid import Grid
from prototype.v2.row_filter import _CPU_LINE, _HW_SPEC, _REQ_VERB, grid_looks_like_inventory
from prototype.v2.text import norm, sig

_TOC_LEADER = re.compile(r"\.{4,}\s*\d+\s*$")

# 부록·서식 — h3 제목 내용으로 판별
_APPENDIX_H3 = re.compile(
    r"서식|붙임|별첨|연혁|이력\s*사항|인력\s*이력|투입\s*인력|"
    r"동의서|확인서|견적서|제안\s*회사|제안업체\s*일반",
    re.I,
)

# 표 '제안 기준' 열 — modal 없이도 기준·스펙 서술
_CRITERION = re.compile(
    r"호환|동등|이상|지원|제공|준수|가\s*능|필수|포함|성능|기능|관리|확장",
    re.I,
)

# 제안 본문 파트 (h3) — 목차 대분류가 제안·요구 영역인지
_PROPOSAL_BODY_H3 = re.compile(
    r"제안요청|요구\s*사항|요청\s*사항|사업\s*내용|제안\s*서\s*작성",
    re.I,
)

# 제목에 '요청/요구/방안 제시' 류가 있으면 수집 맥락
_REQUEST_HEADING = re.compile(
    r"요청|요구|요건|방안|제시|기술하|상세\s*요구|제안\s*기준|고려\s*사항|"
    r"수행\s*방안|관리\s*방안|지원\s*방안|교육|기술이전|제출",
    re.I,
)

# 발주사 인프라·자산 현황 (제안 요청이 아닌 배경 정보)
_INVENTORY_HEADING = re.compile(
    r"당행.*(?:현황|보유|자원)|"
    r"(?:H/?W|클러스터|쿠버네티스).{0,8}현황|"
    r"데이터\s*현황|"
    r"신규\s*도입\s*자원|"
    r"기존\s*당행|"
    r"장비\s*사양|사양\s*표",
    re.I,
)

# 절 도입문 (불릿 없는 (1) … 안내) — 요구 행이 아님
_SECTION_INTRO = re.compile(
    r"^\(\d+\)\s+.+(?:다음|아래|참고|포함하여).{0,20}$",
    re.I,
)

_BULLET = re.compile(r"^[•·▪◦∙\-–—]")


def is_toc_line(text: str) -> bool:
    return bool(_TOC_LEADER.search(norm(text)))


def is_appendix_part(heading: str) -> bool:
    return bool(_APPENDIX_H3.search(norm(heading)))


_CONTEXT_SECTION = re.compile(
    r"사업\s*추진\s*배경|추진\s*배경|추진\s*목적|기대\s*효과|"
    r"사업\s*목적|사업\s*추진\s*일정|제안\s*개요|추진\s*배경",
    re.I,
)
_REQUIREMENT_ZONE = re.compile(
    r"구축\s*방향|요구\s*사항|요청\s*사항|ICT|인프라|정보보호|"
    r"UX|UI|거버넌스|표준|수행\s*방안|BMT|실증",
    re.I,
)


_REFERENCE_SECTION = re.compile(
    r"구성도|"
    r"당사\s*시스템\s*표준|"
    r"(?<!인프라\s)시스템\s*표준|"
    r"실증.*시나리오|평가\s*시나리오|"
    r"제안서\s*작성\s*요령|제안서\s*작성\s*유의",
    re.I,
)


def is_reference_section(heading: str) -> bool:
    """구성도·당사 표준·시나리오·작성요령 — 조견표 요구 본문 아님."""
    h = norm(heading)
    if re.search(r"요구\s*사항|요청\s*사항|기술\s*요건|인프라\s*요구", h, re.I):
        return False
    if re.search(r"(요청|요구|방안|제시)", h, re.I) and not _REFERENCE_SECTION.search(h):
        return False
    return bool(_REFERENCE_SECTION.search(h))


def is_context_section(heading: str) -> bool:
    """배경·목적·효과·일정 — 조견표 본문 아님 (신한 1.2·1.3 등)."""
    h = norm(heading)
    if _REQUIREMENT_ZONE.search(h):
        return False
    return bool(_CONTEXT_SECTION.search(h))


def is_proposal_body_part(heading: str) -> bool:
    return bool(_PROPOSAL_BODY_H3.search(norm(heading)))


def heading_requests_proposal(text: str) -> bool:
    """제목이 제안사에게 무언가를 요구하는 맥락인지."""
    t = norm(text)
    if not t:
        return False
    return bool(_REQUEST_HEADING.search(t))


def heading_is_inventory_context(text: str) -> bool:
    """제목이 발주사 현황·장비 목록 맥락인지 (요청 프레이밍 없을 때)."""
    t = norm(text)
    if not t:
        return False
    if heading_requests_proposal(t):
        return False
    return bool(_INVENTORY_HEADING.search(t))


def text_reads_as_requirement(text: str) -> bool:
    """본문이 제안사에게 기대하는 요구/제안 문장인지."""
    d = norm(text)
    if len(sig(d)) < 2 or is_toc_line(d):
        return False
    # 1) 2) … — 구축방향·요구 리스트 본문 (동사 없어도 요구 행)
    if re.match(r"^\d+\)\s*.+", d) and len(sig(d)) >= 6:
        return True
    if _REQ_VERB.search(d):
        return True
    if _BULLET.match(d) and len(sig(d)) >= 6:
        return True
    if re.search(r"제시|기술|명시|포함|작성|제출|준수", d) and len(sig(d)) >= 12:
        return True
    return False


def text_reads_as_table_criterion(text: str) -> bool:
    """요구표 detail 열 — 동사 없는 제안 기준·스펙 문장."""
    d = norm(text)
    if len(sig(d)) < 6 or is_toc_line(d) or text_reads_as_inventory(d):
        return False
    if text_reads_as_requirement(d):
        return True
    return bool(_CRITERION.search(d)) and len(sig(d)) >= 8


def text_reads_as_inventory(text: str) -> bool:
    """장비 사양·현황 나열 — 요구문이 아님."""
    d = norm(text)
    if not d:
        return False
    if text_reads_as_requirement(d):
        return False
    if _HW_SPEC.search(d):
        return True
    if d.startswith("□") and _CPU_LINE.search(d):
        return True
    if re.match(r"^-\s*\(\d+\)\s*(?:기존|신규)", d):
        return True
    if _CPU_LINE.search(d) and not _REQ_VERB.search(d):
        return True
    return False


def is_section_intro_not_req(text: str) -> bool:
    t = norm(text)
    if _BULLET.match(t):
        return False
    if _SECTION_INTRO.match(t):
        return True
    if t.startswith("(") and "제시하여야" in t and len(t) < 120:
        return True
    return False


def context_allows_collection(headings: dict[int, str], sample: str = "") -> bool:
    """
    현재 heading 스택에서 이 위치의 표/리스트를 수집할지.

    sample: 표 일부 또는 불릿 텍스트 (있으면 본문으로 재판정)
    """
    if not headings:
        return False

    chain = [headings[d] for d in sorted(headings) if norm(headings[d])]
    if not chain or all(is_toc_line(h) for h in chain):
        return False
    if any(is_appendix_part(h) for h in chain):
        return False

    sample = norm(sample)

    if sample and text_reads_as_inventory(sample):
        return False
    if sample and text_reads_as_requirement(sample):
        return True

    inv = [h for h in chain if heading_is_inventory_context(h)]
    if inv and not sample:
        return False
    if inv and sample and not text_reads_as_requirement(sample):
        return False

    if any(is_proposal_body_part(h) for h in chain):
        return True
    if any(heading_requests_proposal(h) for h in chain):
        return True
    return False


def table_passes_content_gate(grid: Grid, headings: dict[int, str]) -> bool:
    if grid_looks_like_inventory(grid):
        return False
    blob = " ".join(
        grid.cells[r][c][:100]
        for r in range(min(grid.nrows, 8))
        for c in range(grid.ncols)
    )
    if not context_allows_collection(headings, blob[:400]):
        return False
    if text_reads_as_inventory(blob) and not _REQ_VERB.search(blob):
        return False
    return True
