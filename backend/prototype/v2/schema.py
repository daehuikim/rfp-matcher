"""
컬럼 역할 스키마 — 단일 선언적 소스(Single Source of Truth).

추출(시스템 표)과 검증(정답 엑셀) 양쪽이 이 스키마 하나로 동작한다.
역할을 바꾸려면 코드가 아니라 이 스키마만 고친다. (하드코딩/fallback 금지)

각 RoleSpec 은 두 종류 신호로 정의:
  - 어휘(header_terms): 헤더 문자열 신호
  - 형상(shape): 데이터 셀의 통계적 특성 신호 (긴 텍스트/반복/짧은 코드)
분류기는 이 신호들을 점수화해 컬럼↔역할을 배정한다 (classify.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoleSpec:
    name: str
    header_terms: tuple[str, ...] = ()
    # 형상 신호 (data-shape) — 어느 통계 특성을 선호하는가
    long_text: bool = False      # 평균 길이가 길수록 가점 (상세/본문)
    short_code: bool = False     # 짧은 코드/번호 비율 높을수록 가점 (ID)
    repeats: bool = False        # 값이 행 사이에서 반복(병합셀 carry)될수록 가점 (분류)
    required: bool = False       # 요구사항 표가 되려면 이 역할이 채워져야 함


# 요구사항(조견표) 스키마. 한국어 RFP 조견표의 컬럼 계위를 선언적으로 기술.
REQUIREMENT_SCHEMA: tuple[RoleSpec, ...] = (
    RoleSpec(
        "id",
        header_terms=("no", "번호", "고유번호", "순번", "요구사항 id", "id"),
        short_code=True,
    ),
    RoleSpec(
        "category",
        header_terms=("구분", "분류", "영역", "기능 그룹", "기능그룹", "통제 구분", "요건 구분", "대분류", "중분류"),
        repeats=True,
    ),
    RoleSpec(
        "item",
        header_terms=("항목", "항목명", "명칭", "요구사항 명칭", "기능명"),
    ),
    RoleSpec(
        "detail",
        header_terms=("상세", "세부", "상세 요건", "상세내역", "상세내용", "상세설명", "내용", "요구사항", "요건", "rfp 요건", "정의", "요구 내용"),
        long_text=True,
        required=True,
    ),
    RoleSpec(
        "note",
        header_terms=("비고", "산출", "관련", "담당", "제안서 반영", "수량", "형식", "가능여부", "금액", "단가"),
    ),
)


def role(name: str) -> RoleSpec:
    for r in REQUIREMENT_SCHEMA:
        if r.name == name:
            return r
    raise KeyError(name)


REQUIRED_ROLES = tuple(r.name for r in REQUIREMENT_SCHEMA if r.required)


# 표가 아닌 리스트/문단형 요구사항을 수집할 때, 그 블록이 '요구사항 섹션'에
# 속하는지 판정하는 선언적 어휘(섹션 헤딩 기준). 과추출 방지용 단일 소스.
SECTION_LEXICON: tuple[str, ...] = (
    "요청 사항", "요청사항", "요구사항", "요구 사항", "요건", "요구", "기능 요건",
    "기능 요구", "기술 요건", "상세 요건", "고려사항", "구축 범위", "제공 기능",
    "프로젝트 범위", "사업 범위", "과업 범위", "구축 대상", "구축 내용",
)


def is_requirement_section(heading: str) -> bool:
    from .text import norm, sig
    hs = sig(heading).lower()
    raw = norm(heading).lower()
    return any(sig(t).lower() in hs or t in raw for t in SECTION_LEXICON)
