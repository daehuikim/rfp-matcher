from __future__ import annotations

from pathlib import Path

import pytest

from prototype.v2.korean_form import (
    extract_korean_form_document,
    is_requirement_id,
    pivot_form_rows,
    split_public_detail,
    summary_as_overview,
)

_REPO = Path(__file__).resolve().parents[3]
_HWP_HTML = (
    _REPO / "temp" / "fss_work" / "금감원제안요청서_24년" / "금감원제안요청서_24년.html"
)
_FSS_ARTIFACT_HTML = (
    _REPO / "data" / "artifacts_final" / "004-금감원" / "work" / "금감원제안요청서_24년.html"
)
_BPC_ARTIFACT_HTML = _REPO / "data" / "artifacts_final" / "003-법제처" / "work" / "005-법제처.html"


def test_is_requirement_id() -> None:
    assert is_requirement_id("FUN-001")
    assert is_requirement_id("SFR-012")
    assert not is_requirement_id("FUN-000")
    assert not is_requirement_id("기능 요구사항")
    assert not is_requirement_id("IPv4")
    assert not is_requirement_id("IPv6")


def test_continuation_with_related_req_id() -> None:
    """세부내용 분할 표 — 관련요구사항 DAR-006 이 있어도 continuation."""
    from prototype.v2.korean_form import _is_form_continuation
    from prototype.v2.grid import Grid

    cells = [
        ["", "세부내용", "◦ 연혁법령 검색 포함"],
        ["산출정보", "산출정보", ""],
        ["관련요구사항", "관련요구사항", "DAR-006"],
    ]
    g = Grid(cells=cells, table_id=20, page=None, section_heading="")
    assert _is_form_continuation(g)


def test_split_public_detail_preserves_pipe_table() -> None:
    """DAR-009 — expand 전 pipe 표 헤더(순번|분류|구성방안)가 분리되지 않음."""
    detail = (
        "◦ AI 모델의 평가 수행을 위한 데이터셋 구축 - 구축 대상은 발주기관과 협의하여 구성"
        "<평가 데이터셋 예시>\n"
        "순번 | 분류 | 구성방안\n"
        "--- | --- | ---\n"
        "1 | 검색 | 질문-법령정보별 검색결과\n"
        "2 | 요약 | 원문-요약문 구성"
    )
    pieces = split_public_detail(detail)
    assert len(pieces) == 1
    assert "순번 | 분류 | 구성방안" in pieces[0]
    assert "질문-법령정보별 검색결과" in pieces[0]


def test_pivot_sfr002_agent_table() -> None:
    """SFR-002 — 분류|수행 업무 2열 에이전트 표 → pipe markdown."""
    rows = [
        ["요구사항 고유번호", "SFR-002"],
        ["요구사항 명칭", "멀티 에이전트"],
        ["요구사항 상세설명", "정의", "워크플로우"],
        [
            "세부내용",
            "◦ AI 에이전트 구성 정의분류수행 업무의도 분석 에이전트질의 재구성",
            "",
        ],
        ["분류", "수행 업무"],
        ["의도 분석 에이전트", "질의 재구성"],
        ["검색 에이전트", "GraphRAG 검색"],
        ["산출정보", ""],
    ]
    req = pivot_form_rows(rows, doc_name="t", table_id=1)
    assert req is not None
    assert "분류 | 수행 업무" in req.detail
    assert "의도 분석 에이전트" in req.detail
    assert "정의분류수행 업무" not in req.detail


def test_pivot_psr014_checkpoint_table() -> None:
    """PSR-014 — <주요 점검항목 예시> 캡션 + 번호|점검분야 표."""
    rows = [
        ["요구사항 고유번호", "PSR-014"],
        ["요구사항 명칭", "AI 검토"],
        ["세부내용", "◦ 체크리스트 수행<주요 점검항목 예시>번호점검분야1설계사업의 타당성"],
        ["<주요 점검항목 예시>"],
        ["번호", "점검분야", "점검항목", "산출물"],
        ["1", "설계", "목표서비스 및 필요성", "사업의 타당성"],
        ["2", "2. 기술의 안전성"],
        ["산출정보"],
    ]
    req = pivot_form_rows(rows, doc_name="t", table_id=1, require_detail=False)
    assert req is not None
    assert "번호 | 점검분야" in req.detail
    assert "사업의 타당성" in req.detail
    assert "번호점검분야1설계" not in req.detail


def test_pivot_embedded_table_rows() -> None:
    """DAR-009 — HWPX mega-table 내 표 행 → pipe markdown."""
    rows = [
        ["요구사항 고유번호", "DAR-009", ""],
        ["요구사항 명칭", "평가 데이터셋 구축", ""],
        ["요구사항 상세설명", "정의", "평가용 데이터셋"],
        [
            "세부내용",
            "◦ 평가 데이터셋 구축<평가 데이터셋 예시>순번분류구성방안1검색질문-법령정보별 검색결과",
            "",
        ],
        ["순번", "분류", "구성방안"],
        ["1", "검색", "질문-법령정보별 검색결과"],
        ["2", "요약", "원문-요약문 구성"],
        ["산출정보", "", ""],
    ]
    req = pivot_form_rows(rows, doc_name="t", table_id=1)
    assert req is not None
    assert "순번 | 분류 | 구성방안" in req.detail
    assert "---" in req.detail
    assert "질문-법령정보별 검색결과" in req.detail
    assert "순번분류구성방안1검색" not in req.detail


def test_pivot_definition_with_inline_bullet() -> None:
    """DAR-001 — 정의 셀에 ◦ 불릿·세부내용 '하여야 함' 꼬리 조각."""
    rows = [
        ["요구사항 분류", "요구사항 분류", "데이터", "요구사항 번호", "DAR-001"],
        ["요구사항 명칭", "요구사항 명칭", "DB 모델링", "DB 모델링", "DB 모델링"],
        [
            "요구사항 상세설명",
            "정의",
            "데이터 모델 설계 및 데이터 이관 ◦데이터 모델은 표준 사전에 따라 설계",
            "데이터 모델 설계 및 데이터 이관 ◦데이터 모델은 표준 사전에 따라 설계",
            "데이터 모델 설계 및 데이터 이관 ◦데이터 모델은 표준 사전에 따라 설계",
        ],
        [
            "요구사항 상세설명",
            "세부 내용",
            "하여야 함 ◦논리 데이터 모델은 중복을 최소화하여야 함 ◦물리 데이터 모델은 성능을 보장하여야 함",
            "하여야 함 ◦논리 데이터 모델은 중복을 최소화하여야 함 ◦물리 데이터 모델은 성능을 보장하여야 함",
            "하여야 함 ◦논리 데이터 모델은 중복을 최소화하여야 함 ◦물리 데이터 모델은 성능을 보장하여야 함",
        ],
        ["산출물", "산출물", "ERD", "ERD", "ERD"],
    ]
    req = pivot_form_rows(rows, doc_name="t", table_id=1)
    assert req is not None
    assert req.mid == "데이터 모델 설계 및 데이터 이관"
    assert "표준 사전에 따라 설계" in req.detail and "하여야 함" in req.detail
    assert "논리 데이터 모델" in req.detail
    assert "물리 데이터 모델" in req.detail


def test_pivot_wide_form_block() -> None:
    rows = [
        ["요구사항 분류", "요구사항 분류", "기능", "요구사항 번호", "FUN-001"],
        ["요구사항 명칭", "요구사항 명칭", "테스트 명칭", "테스트 명칭", "테스트 명칭"],
        ["요구사항 상세설명", "정의", "정의 본문", "정의 본문", "정의 본문"],
        ["요구사항 상세설명", "세부 내용", "◦ 세부 내용 본문", "◦ 세부 내용 본문", "◦ 세부 내용 본문"],
        ["산출물", "산출물", "개발 단계별 산출물", "개발 단계별 산출물", "개발 단계별 산출물"],
    ]
    req = pivot_form_rows(rows, doc_name="t", table_id=1)
    assert req is not None
    assert req.rid == "FUN-001"
    assert req.tab == "FUN"
    assert req.top == "테스트 명칭"
    assert req.mid == "정의 본문"
    assert "세부 내용" in req.detail
    assert req.deliverable == "개발 단계별 산출물"


@pytest.mark.skipif(not _FSS_ARTIFACT_HTML.is_file(), reason="금감원 artifact HTML 없음")
def test_psr005_no_proposal_appendix_bleed() -> None:
    """PSR-005 뒤 제안서·목차 표가 마지막 요구사항에 붙지 않음."""
    html = _FSS_ARTIFACT_HTML.read_text(encoding="utf-8")
    result = extract_korean_form_document(html, "금감원")
    assert result is not None
    psr5 = [r for r in result.reqs if r.rid == "PSR-005"]
    assert len(psr5) == 1
    assert "제안개요" not in psr5[0].detail
    assert "목 차" not in psr5[0].detail
    assert all("제안개요" not in r.detail for r in result.reqs)


@pytest.mark.skipif(not _BPC_ARTIFACT_HTML.is_file(), reason="법제처 artifact HTML 없음")
def test_sfr002_psr014_pipe_tables_in_extract() -> None:
    """SFR-002·PSR-014 — 추출 결과에 pipe 표 보존(인라인 평탄화 제거)."""
    html = _BPC_ARTIFACT_HTML.read_text(encoding="utf-8")
    result = extract_korean_form_document(html, "법제처")
    sfr2 = next(r for r in result.reqs if r.rid == "SFR-002")
    psr14 = [r for r in result.reqs if r.rid == "PSR-014"]
    assert "분류 | 수행 업무" in sfr2.detail
    assert "정의분류수행 업무" not in sfr2.detail
    assert any("번호 | 점검분야" in r.detail for r in psr14)
    assert not any("번호점검분야점검항목" in r.detail for r in psr14)


@pytest.mark.skipif(not _BPC_ARTIFACT_HTML.is_file(), reason="법제처 artifact HTML 없음")
def test_orphan_tables_and_no_ipv_tab() -> None:
    """orphan 표 재결합 — SFR-004 표 복원, SFR-021/DAR-026 오염·IPV 탭 없음."""
    html = _BPC_ARTIFACT_HTML.read_text(encoding="utf-8")
    result = extract_korean_form_document(html, "법제처")
    tabs = {r.tab for r in result.reqs}
    assert "IPV" not in tabs
    sfr4 = next(r for r in result.reqs if r.rid == "SFR-004")
    assert "분 류 | 내 용" in sfr4.detail or "분류 | 내용" in sfr4.detail
    assert "자동 연계" in sfr4.detail
    sfr21 = [r for r in result.reqs if r.rid == "SFR-021"]
    assert not any("의도 분석 에이전트" in r.detail for r in sfr21)
    dar26 = [r for r in result.reqs if r.rid == "DAR-026"]
    assert not any("순번 | 분류 | 구성방안" in r.detail for r in dar26)


@pytest.mark.skipif(not _HWP_HTML.is_file(), reason="금감원 hwp5html 산출물 없음")
def test_extract_gold_gap_hwpx_html() -> None:
    html = _HWP_HTML.read_text(encoding="utf-8")
    result = extract_korean_form_document(html, "금감원24")
    assert result is not None
    assert result.summary is not None
    assert "FUN" in result.summary.tab_order
    assert len(result.reqs) >= 30
    tabs = {r.tab for r in result.reqs if r.tab != "부록"}
    assert "FUN" in tabs
    assert all(r.rid for r in result.reqs if r.tab != "부록")
    ov = summary_as_overview(result.summary)
    assert ov["type"] == "summary_table"
    assert ov["sheet_title"] == "요구사항 총괄표"
