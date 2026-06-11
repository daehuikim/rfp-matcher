from __future__ import annotations

import pytest

from prototype.v2.extract import Req
from prototype.v2.ids import assign_ids, id_prefix
from prototype.v2.tab_naming import (
    coarse_tab_from_section,
    is_bad_tab_name,
    sanitize_hierarchy_label,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        (
            "II. 제안 요청 사항 > 4. 정보보호 요청사항 > 4.2. 정보처리시스템 기능그룹별 정보보호 요구사항",
            "정보보호 요청사항",
        ),
        (
            "II. 제안 요청 사항 > 4. 정보보호 요청사항 > 4.6. AI 플랫폼 보안 요건",
            "정보보호 요청사항",
        ),
        (
            "II. 제안 요청 사항 > 2. ICT 요청사항 > 2.4. 프로젝트 업무 및 기술 요건",
            "프로젝트 업무 및 기술 요건",
        ),
        (
            "II. 제안 요청 사항 > 2. ICT 요청사항 > 2.5. ICT 인프ra 요구사항",
            "ICT 인프ra 요구사항",
        ),
        (
            "II. 제안 요청 사항 > 5. AI 거버넌스 요청사항 > 5.2. AI(인공지능) 시스템/서비스 개발 시 공통/필수적 이행사항",
            "AI 거버넌스 요청사항",
        ),
        (
            "II. 제안 요청 사항 > 1. 제안 요청 범위 (사업 범위) > 1.1. 구축방향",
            "구축방향",
        ),
    ],
)
def test_coarse_tab_shinhan(path: str, expected: str) -> None:
    assert coarse_tab_from_section(path) == expected


def test_coarse_tab_jb_sentence_heading() -> None:
    sp = "2. 프로젝트 목적 본 사업은 JB금융그룹의 AI 기반 디지털 전환 가속화를 위해"
    assert coarse_tab_from_section(sp) == "프로젝트 목적"


def test_is_bad_tab_name() -> None:
    assert is_bad_tab_name("상세 요구사항 다음의 상세 요구사항을 참고하시기 바랍니다.")
    assert not is_bad_tab_name("ICT 인프ra 요구사항")
    assert is_bad_tab_name("프로젝트 목적 본 사업은 JB금융그룹의 AI 기반")


def test_sanitize_hierarchy_label() -> None:
    assert sanitize_hierarchy_label("1") == ""
    assert sanitize_hierarchy_label("2)") == ""
    assert sanitize_hierarchy_label("시스템 일반") == "시스템 일반"


def test_assign_ids_uses_top() -> None:
    reqs = [
        Req(
            doc="t",
            table_id=1,
            page=1,
            tab="프로젝트 업무 및 기술 요건",
            top="시스템 일반",
            mid="서버구성 방안",
            detail="본문",
        ),
        Req(
            doc="t",
            table_id=1,
            page=1,
            tab="프로젝트 업무 및 기술 요건",
            top="시스템 일반",
            mid="",
            detail="세부",
        ),
    ]
    assign_ids(reqs)
    assert reqs[0].rid == "시스템_001"
    assert reqs[1].rid == "시스템_002"


def test_id_prefix_not_tab_slug() -> None:
    r = Req(
        doc="t",
        table_id=1,
        page=1,
        tab="당행 아키텍처 준수 방안",
        top="표준 아키텍처 준수",
        mid="H/W 구성",
        detail="x",
    )
    assert id_prefix(r) == "인프라아키텍처"
