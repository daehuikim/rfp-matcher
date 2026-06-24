"""요건구분 폴백·셀 불릿 분리."""
from prototype.v3.financial_heading import lowest_category_label
from prototype.v3.financial_rfp import _Ctx, _split_dash_bullet_cell
from prototype.v3.financial_content import is_reference_section


def test_lowest_category_fallback():
    sp = "1.4. 프로젝트 범위 > (2) 상세 요구사항 내용 > (3) 쿠버네티스 클러스터 현황"
    assert lowest_category_label(sp) == "(3) 쿠버네티스 클러스터 현황"
    ctx = _Ctx(headings={4: "1.4. 프로젝트 범위", 6: "(3) 쿠버네티스 클러스터 현황"})
    assert ctx.req_group() == "(3) 쿠버네티스 클러스터 현황"


def test_split_dash_bullet_cell():
    text = (
        "인프라 구축을 위한 인력이 제안되어야 함\n"
        " - 인프라 설치, 구성\n"
        " - 인프라 변경 작업"
    )
    parts = _split_dash_bullet_cell(text)
    assert len(parts) == 2
    assert "제안되어야 함" in parts[0]
    assert parts[1].startswith("- 인프라 변경")


def test_reference_section():
    assert is_reference_section("2.3. 당사 시스템 표준")
    assert is_reference_section("▪ 실증기반 평가 시나리오")
    assert not is_reference_section("2.5. ICT 인프라 요구사항")
    assert not is_reference_section("2.4. 프로젝트 업무 및 기술 요건")
