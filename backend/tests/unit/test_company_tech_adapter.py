from __future__ import annotations

from app.domain.enums import Judgement
from app.phase2.company_tech.adapter import (
    build_catalog_audit,
    extract_missing_tech,
    format_related_solution,
    kt_holdings_labels,
    resolve_adopted_chunk_ids,
    to_recommendation,
)
from app.phase2.company_tech.models import (
    InternalReviewResult,
    QueryRouting,
    SearchResult,
    TechnicalReview,
)


def test_kt_holdings_from_selected_sources() -> None:
    labels = kt_holdings_labels(["rag.txt", "agent.txt"], "circle")
    assert labels == ["K RAG", "K Agent"]


def test_missing_tech_from_gaps_on_triangle() -> None:
    review = TechnicalReview(
        status="triangle",
        status_reason="표 인식은 가능하나 실시간 스트리밍 OCR 근거가 불명확합니다.",
        gaps=["실시간 스트리밍 OCR", "대용량 배치 파이프라인"],
        unsupported_items=[],
        matched_capabilities=[],
        strengths=[],
        missing_tech=[],
        recommendation="",
        referenced_sources=[],
    )
    query = "표 인식 및 실시간 스트리밍 OCR 지원"
    missing = extract_missing_tech(review, query)
    assert "실시간 스트리밍 OCR" in missing
    assert "대용량 배치 파이프라인" not in missing
    assert len(missing) <= 2


def test_kt_holdings_empty_when_no_routed_sources() -> None:
    assert kt_holdings_labels([], "circle") == []
    assert kt_holdings_labels([], "triangle") == []


def test_missing_tech_rejects_requirement_paraphrase() -> None:
    review = TechnicalReview(
        status="triangle",
        status_reason="문서 OCR은 가능하나 연동 API 근거가 부족합니다.",
        gaps=["표 인식 기능을 제공해야 함", "시스템 구축 및 운영"],
        unsupported_items=["요구사항에 명시된 실시간 처리 지원"],
        matched_capabilities=[],
        strengths=[],
        missing_tech=[],
        recommendation="",
        referenced_sources=[],
    )
    missing = extract_missing_tech(review, "표 인식 및 실시간 처리 지원")
    assert missing == []


def test_missing_tech_rejects_vague_single_words() -> None:
    review = TechnicalReview(
        status="triangle",
        status_reason="RAI Red Teaming 근거만 있고 AX 로드맵 제시 근거는 약합니다.",
        gaps=[],
        unsupported_items=[],
        matched_capabilities=[],
        strengths=[],
        missing_tech=["Red", "Teaming", "AX"],
        recommendation="",
        referenced_sources=[],
    )
    query = "확장 가능한 AX 로드맵 제시"
    assert extract_missing_tech(review, query) == []


def test_missing_tech_filters_weak_padding_items() -> None:
    review = TechnicalReview(
        status="triangle",
        status_reason="일부 기능만 지원",
        gaps=[],
        unsupported_items=[],
        matched_capabilities=[],
        strengths=[],
        missing_tech=["연동 API", "운영 지원", "기능 보완", "확인 필요"],
        recommendation="",
        referenced_sources=[],
    )
    query = "외부 시스템 연동 API 및 문서 OCR 지원"
    missing = extract_missing_tech(review, query)
    assert missing == ["연동 API"]
    assert len(missing) <= 2


def test_missing_tech_prefers_llm_missing_tech_field() -> None:
    review = TechnicalReview(
        status="x",
        status_reason="의료 영상 분석 근거가 부족합니다.",
        gaps=["Solar LLM"],
        unsupported_items=[],
        matched_capabilities=[],
        strengths=[],
        missing_tech=["FDA 인증 모듈", "의료영상 라벨링"],
        recommendation="",
        referenced_sources=[],
    )
    query = "X-ray 영상 자동 판독 및 의료영상 분석"
    missing = extract_missing_tech(review, query)
    assert "FDA 인증 모듈" in missing
    assert "Solar LLM" not in missing


def test_circle_adopts_all_evidence_when_refs_empty() -> None:
    evidence = [
        SearchResult(
            chunk_id="studio-0",
            document="AX 플랫폼",
            metadata={"source_file": "intelligence-studio.txt", "chunk_index": 0},
            bm25_score=2.0,
        ),
        SearchResult(
            chunk_id="studio-1",
            document="모듈 조합",
            metadata={"source_file": "intelligence-studio.txt", "chunk_index": 1},
            bm25_score=1.5,
        ),
    ]
    review = TechnicalReview(
        status="circle",
        status_reason="내부 근거가 AX 로드맵 제시를 지원합니다.",
        referenced_sources=[],
    )
    adopted = resolve_adopted_chunk_ids(review, evidence)
    assert adopted == {"studio-0", "studio-1"}
    audit = build_catalog_audit(evidence, review)
    assert all(item.selected for item in audit)


def test_triangle_adopts_top_evidence_when_refs_empty() -> None:
    evidence = [
        SearchResult(
            chunk_id="low",
            document="약한 근거",
            metadata={"source_file": "rag.txt", "chunk_index": 0},
            bm25_score=1.0,
        ),
        SearchResult(
            chunk_id="high",
            document="강한 근거",
            metadata={"source_file": "rag.txt", "chunk_index": 1},
            bm25_score=4.0,
        ),
    ]
    review = TechnicalReview(
        status="triangle",
        status_reason="일부만 지원",
        referenced_sources=[],
    )
    adopted = resolve_adopted_chunk_ids(review, evidence)
    assert adopted == {"high"}


def test_x_adopts_none_when_refs_empty() -> None:
    evidence = [
        SearchResult(
            chunk_id="rag-0",
            document="무관",
            metadata={"source_file": "rag.txt", "chunk_index": 0},
            bm25_score=2.0,
        ),
    ]
    review = TechnicalReview(
        status="x",
        status_reason="근거 없음",
        referenced_sources=[],
    )
    assert resolve_adopted_chunk_ids(review, evidence) == set()


def test_format_related_solution_for_triangle() -> None:
    review = TechnicalReview(
        status="triangle",
        status_reason="DocuSee OCR 지원 가능",
        matched_capabilities=["DocuSee OCR", "표 구조 분석"],
        referenced_sources=[],
    )
    text = format_related_solution(review, ["rag.txt"])
    assert "대분류: K RAG(rag.txt)" in text
    assert "세부내용: DocuSee OCR; 표 구조 분석" in text


def test_format_related_solution_for_x() -> None:
    review = TechnicalReview(status="x", status_reason="근거 없음")
    assert format_related_solution(review, ["rag.txt"]) == "해당 없음"


def test_to_recommendation_maps_ui_fields() -> None:
    result = InternalReviewResult(
        review=TechnicalReview(
            status="triangle",
            status_reason="DocuSee로 문서 OCR은 가능하나 연동 API 근거가 부족합니다.",
            gaps=["연동 API"],
            unsupported_items=[],
            matched_capabilities=["DocuSee OCR"],
            strengths=[],
            missing_tech=[],
            recommendation="PoC 범위 확인 필요",
            referenced_sources=["rag.txt:2"],
        ),
        routing=QueryRouting(selected_sources=["rag.txt"], reasoning="RAG 관련"),
        selected_sources=["rag.txt"],
        evidence_results=[
            SearchResult(
                chunk_id="rag-1",
                document="DocuSee OCR 지원",
                metadata={"source_file": "rag.txt", "chunk_index": 2, "section_title": "DocuSee"},
                bm25_rank=1,
                bm25_score=3.5,
            )
        ],
    )
    rec = to_recommendation(
        "req-1",
        result,
        query="외부 시스템 연동 API 및 문서 OCR 지원",
    )
    assert rec.ai_risk == Judgement.PARTIAL
    assert rec.ai_reason.startswith("DocuSee")
    assert "대분류: K RAG(rag.txt)" in rec.related_solution
    assert rec.matched_solutions == ["K RAG"]
    assert "연동 API" in rec.missing_tech
    assert len(rec.catalog_audit) == 1
    assert rec.catalog_audit[0].solution_name == "K RAG"
    assert rec.catalog_audit[0].selected is True
