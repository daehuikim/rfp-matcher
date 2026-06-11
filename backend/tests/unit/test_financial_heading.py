"""금융 heading — 절 번호 유지."""
from __future__ import annotations

from prototype.v3.financial_heading import (
    is_numbered_section_title,
    is_valid_req_group_label,
    preserve_group_label,
    split_title_body,
    tab_from_section_path,
)


def test_preserve_group_label_keeps_section_number() -> None:
    assert preserve_group_label("2.7.4. 유지보수 이행방안") == "2.7.4. 유지보수 이행방안"
    assert preserve_group_label("2.3. 프로젝트 수행방안") == "2.3. 프로젝트 수행방안"
    assert preserve_group_label("2.3.2. 기타 요구사항") == "2.3.2. 기타 요구사항"


def test_numbered_section_title() -> None:
    assert is_numbered_section_title("2.7.4. 유지보수 이행방안")
    assert not is_numbered_section_title(
        "상시 및 유사시에 대한 유지보수에 대하여 다음과 같이 요청하며"
    )


def test_tab_merges_14_and_143() -> None:
    sp = "1. 개요 > 1.4. 프로젝트 범위 > 1.4.3. 상세 요구사항"
    assert tab_from_section_path(sp) == "1.4. 프로젝트 범위"


def test_narrative_not_req_group() -> None:
    assert not is_valid_req_group_label(
        "프로젝트에서 준수해야 할 표준 H/W 아키텍처 구성요소는 다음과 같으며"
    )
    assert is_valid_req_group_label("2.3.3. 당행 아키텍처 준수 방안")
    assert is_valid_req_group_label("데이터 수집")


def test_split_title_body_gita() -> None:
    title, body = split_title_body(
        "2.3.2. 기타 요구사항 본 프로젝트와 관련하여 기타 혹은 특별 요구사항에 대하여 요청합니다."
    )
    assert title == "2.3.2. 기타 요구사항"
    assert body.startswith("본 프로젝트")
    t2, b2 = split_title_body(
        "1.4.4. 기타 요청사항 상기 요구사항 외 충족하여야 할 내용을 참고하시기 바랍니다."
    )
    assert t2 == "1.4.4. 기타 요청사항"
    assert b2.startswith("상기")
