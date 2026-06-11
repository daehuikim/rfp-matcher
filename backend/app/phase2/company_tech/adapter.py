from __future__ import annotations

import re

from app.domain.enums import Judgement
from app.domain.models import CatalogCandidateAudit, MatchedSolutionSku, Recommendation
from app.phase2.company_tech.constants import SOURCE_LABELS, TECH_VOCAB
from app.phase2.company_tech.models import InternalReviewResult, SearchResult, TechnicalReview

_STATUS_TO_JUDGEMENT = {
    "circle": Judgement.YES,
    "triangle": Judgement.PARTIAL,
    "x": Judgement.NO,
}

_MAX_MISSING_TECH = 2
_MIN_MISSING_TECH_SCORE = 3

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*|[가-힣]{2,}")

_VAGUE_MISSING = frozenset({
    "ax",
    "ai",
    "red",
    "team",
    "teaming",
    "studio",
    "cloud",
    "agent",
    "model",
    "data",
    "보안",
    "로드맵",
    "플랫폼",
    "솔루션",
    "서비스",
    "시스템",
    "기능",
    "운영",
    "구축",
    "지원",
    "제공",
    "관리",
    "개발",
    "확장",
    "연동",
    "연계",
    "통합",
    "분석",
    "처리",
})

_GENERIC_QUERY_STOP = frozenset({
    "시스템",
    "서비스",
    "기능",
    "지원",
    "구축",
    "운영",
    "제공",
    "필요",
    "가능",
    "기술",
    "솔루션",
    "플랫폼",
    "데이터",
    "관리",
    "개발",
    "설계",
    "구현",
    "요구",
    "요구사항",
    "항목",
    "대한",
    "위한",
    "통한",
    "및",
    "등",
    "수행",
    "제시",
    "확보",
    "보유",
    "이용",
    "사용",
    "적용",
    "대응",
    "확인",
    "검토",
    "내부",
    "외부",
    "기업",
    "고객",
    "업무",
    "프로세스",
    "환경",
    "방안",
    "방법",
    "내용",
    "수준",
    "기준",
    "조건",
    "범위",
    "목적",
    "성능",
    "품질",
    "보안",
    "안정",
    "가용",
    "확장",
    "연계",
    "연동",
    "통합",
    "처리",
    "분석",
    "생성",
    "자동",
    "실시간",
    "기반",
    "맞춤",
    "맞춤형",
    "표준",
    "전체",
    "부분",
    "관련",
    "포함",
    "이상",
    "이하",
    "경우",
    "때문",
    "있음",
    "없음",
    "가능한",
    "필요한",
    "지원하는",
    "제공하는",
})

_TECH_STOP = frozenset({
    "때문",
    "경우",
    "부분",
    "기술",
    "근거",
    "요구",
    "지원",
    "불명확",
    "직접",
    "관련",
    "가능",
    "불가",
    "필요",
    "확인",
    "부족",
    "미지원",
    "없음",
    "있음",
    "등",
    "전체",
    "내부",
})

_REQ_PHRASE_MARKERS = (
    "요구사항",
    "사용자 요청",
    "요청 기술",
    "기능을",
    "기능 제공",
    "시스템 구축",
    "서비스를",
    "지원할",
    "지원해야",
    "제공해야",
    "구축해야",
    "구현해야",
    "필수",
    "충족",
    "준수",
    "납품",
    "수행할",
    "보장",
    "제출",
)

_TECH_MARKER_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9_+-]{1,}|"
    r"\d+\s*(?:GB|TB|MB|Mbps|Gbps|ms|Hz|core|코어)|"
    r"(?:OCR|API|SDK|GPU|CPU|LLM|RAG|TEE|CMK|HSM|vLLM|Kubernetes|"
    r"HuggingFace|OpenAI|Terraform|IaC|MCP|VM|SLA|JSON|HTTP|REST|gRPC)",
    re.IGNORECASE,
)

_TECH_SUFFIX_RE = re.compile(
    r"(?:API|OCR|SDK|GPU|LLM|RAG|VM|HSM|TEE|CMK|DB|UI|"
    r"파이프라인|임베딩|청킹|라우팅|오케스트레이터|모니터링|검색기|리랭커)$",
    re.IGNORECASE,
)

_VOCAB_BY_LENGTH = sorted(
    (term for term in TECH_VOCAB if len(term) >= 3),
    key=len,
    reverse=True,
)


def _dedupe(items: list[str], limit: int = _MAX_MISSING_TECH) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
        if len(out) >= limit:
            break
    return out


def _matches_tech_vocab(phrase: str) -> bool:
    normalized = phrase.casefold()
    if len(normalized) < 3:
        return False
    for term in _VOCAB_BY_LENGTH:
        if term in normalized or normalized in term:
            return True
    return False


def _has_technical_markers(phrase: str) -> bool:
    return bool(_TECH_MARKER_RE.search(phrase)) or bool(_TECH_SUFFIX_RE.search(phrase))


def _looks_like_requirement_text(phrase: str) -> bool:
    if any(marker in phrase for marker in _REQ_PHRASE_MARKERS):
        return True
    if any(marker in phrase for marker in ("습니다", "합니다", "있으나", "하지만", "그러나", "하며", "하여")):
        return True
    if len(phrase) > 24 and not _has_technical_markers(phrase) and not _matches_tech_vocab(phrase):
        return True
    return False


def _meaningful_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        token = raw.casefold()
        if len(token) < 2 or token in _GENERIC_QUERY_STOP or token in _TECH_STOP:
            continue
        tokens.add(token)
    return tokens


def _is_single_token_phrase(phrase: str) -> bool:
    return " " not in phrase and "·" not in phrase and "/" not in phrase and "+" not in phrase


def _repeats_only_query_keywords(phrase: str, query: str) -> bool:
    phrase_text = phrase.strip()
    query_text = query.strip()
    if not phrase_text or not query_text:
        return False

    phrase_lower = phrase_text.casefold()
    if phrase_lower in _VAGUE_MISSING:
        return True

    # 복합 명사구는 catalog처럼 LLM이 지정한 갭으로 허용
    if not _is_single_token_phrase(phrase_text):
        return False

    phrase_tokens = _meaningful_tokens(phrase_text)
    query_tokens = _meaningful_tokens(query_text)
    if not phrase_tokens:
        return True
    if phrase_tokens <= query_tokens:
        return True
    if phrase_lower in {t.casefold() for t in query_tokens}:
        return True
    return False


def _is_vague_single_word(phrase: str) -> bool:
    text = phrase.strip()
    if not _is_single_token_phrase(text):
        return False
    lower = text.casefold()
    if lower in _VAGUE_MISSING or lower in _TECH_STOP:
        return True
    if text.isascii() and len(text) <= 5 and not _has_technical_markers(text):
        return True
    return False


def _is_llm_missing_tech_item(phrase: str, query: str) -> bool:
    text = phrase.strip()
    if len(text) < 3 or len(text) > 40:
        return False
    if _looks_like_requirement_text(text):
        return False
    if _is_vague_single_word(text):
        return False
    if _repeats_only_query_keywords(text, query):
        return False
    return True


def _missing_tech_core_score(phrase: str, query: str) -> int:
    """핵심 갭일수록 높은 점수 — 개수 채우기용 약한 항목은 걸러냄."""
    text = phrase.strip()
    score = 0
    if _matches_tech_vocab(text):
        score += 3
    if _has_technical_markers(text):
        score += 2
    if not _is_single_token_phrase(text) and len(text) >= 6:
        score += 1
    if _meaningful_tokens(text) & _meaningful_tokens(query):
        score += 1
    return score


def _is_core_missing_tech(phrase: str, query: str, *, require_query_overlap: bool = True) -> bool:
    if not _is_llm_missing_tech_item(phrase, query):
        return False
    if _is_single_token_phrase(phrase):
        return _matches_tech_vocab(phrase) or _has_technical_markers(phrase)
    if require_query_overlap and not (_meaningful_tokens(phrase) & _meaningful_tokens(query)):
        return False
    return _missing_tech_core_score(phrase, query) >= _MIN_MISSING_TECH_SCORE


def _select_core_missing_tech(candidates: list[str], query: str) -> list[str]:
    ranked = sorted(
        ((phrase, _missing_tech_core_score(phrase, query)) for phrase in candidates),
        key=lambda item: (-item[1], len(item[0])),
    )
    out: list[str] = []
    for phrase, score in ranked:
        if score < _MIN_MISSING_TECH_SCORE:
            continue
        if phrase not in out:
            out.append(phrase)
        if len(out) >= _MAX_MISSING_TECH:
            break
    return out


def extract_missing_tech(review: TechnicalReview, query: str = "") -> list[str]:
    """△/X일 때 핵심 기술 갭만 최대 2개. 없으면 빈 배열."""
    if review.status == "circle":
        return []

    primary = [
        item.strip()
        for item in review.missing_tech
        if _is_core_missing_tech(item.strip(), query, require_query_overlap=False)
    ]
    if primary:
        return _select_core_missing_tech(primary, query)

    fallback = [
        item.strip()
        for item in [*review.gaps, *review.unsupported_items]
        if _is_core_missing_tech(item.strip(), query, require_query_overlap=True)
    ]
    return _select_core_missing_tech(fallback, query)


def format_related_solution(review: TechnicalReview, selected_sources: list[str]) -> str:
    if review.status == "x":
        return "해당 없음"

    categories = [
        f"{SOURCE_LABELS.get(source, source)}({source})"
        for source in selected_sources
    ]
    category_text = ", ".join(categories) if categories else "전체 내부 기술"
    capabilities = "; ".join(review.matched_capabilities) or review.status_reason
    return f"대분류: {category_text}\n세부내용: {capabilities}"


def kt_holdings_labels(selected_sources: list[str], status: str) -> list[str]:
    """우리 연관 솔루션의 대분류 → KT 보유 기술 (라우팅된 소스만)."""
    if status == "x" or not selected_sources:
        return []
    return [SOURCE_LABELS.get(source, source.replace(".txt", "")) for source in selected_sources]


def _similarity_score(result: SearchResult) -> float:
    if result.bm25_score is not None:
        return float(result.bm25_score)
    if result.vector_distance is not None:
        return max(0.0, 1.0 / (1.0 + float(result.vector_distance)))
    return 0.0


def _chunk_ref_keys(result: SearchResult) -> set[str]:
    keys = {result.chunk_id}
    source = str(result.metadata.get("source_file", ""))
    chunk_index = result.metadata.get("chunk_index")
    if source and chunk_index is not None:
        keys.add(f"{source}:{chunk_index}")
    return keys


def _resolve_llm_referenced_chunk_ids(
    referenced_sources: list[str],
    evidence: list[SearchResult],
) -> set[str]:
    if not referenced_sources or not evidence:
        return set()

    ref_set = {item.strip() for item in referenced_sources if item.strip()}
    by_key: dict[str, str] = {}
    for result in evidence:
        for key in _chunk_ref_keys(result):
            by_key[key] = result.chunk_id

    index_to_ids: dict[str, list[str]] = {}
    for result in evidence:
        chunk_index = result.metadata.get("chunk_index")
        if chunk_index is not None:
            index_to_ids.setdefault(str(chunk_index), []).append(result.chunk_id)

    adopted: set[str] = set()
    for ref in ref_set:
        if ref in by_key:
            adopted.add(by_key[ref])
            continue
        if ref.isdigit():
            for chunk_id in index_to_ids.get(ref, []):
                adopted.add(chunk_id)
    return adopted


def resolve_adopted_chunk_ids(
    review: TechnicalReview,
    evidence: list[SearchResult],
) -> set[str]:
    """판정(O/△/X)과 검색 내역 채택을 일치시킨다."""
    if not evidence:
        return set()

    adopted = _resolve_llm_referenced_chunk_ids(review.referenced_sources, evidence)
    if adopted:
        return adopted

    if review.status == "x":
        return set()

    ranked = sorted(evidence, key=_similarity_score, reverse=True)
    if review.status == "circle":
        # O = 검색된 내부 근거가 요구를 지원 → 가져온 chunk 전부 채택
        return {result.chunk_id for result in evidence}

    # △ = 부분 지원 → 가장 관련 높은 근거 1개 채택
    return {ranked[0].chunk_id}


def build_catalog_audit(
    evidence: list[SearchResult],
    review: TechnicalReview,
) -> list[CatalogCandidateAudit]:
    adopted_ids = resolve_adopted_chunk_ids(review, evidence)
    audits: list[CatalogCandidateAudit] = []
    for result in evidence:
        meta = result.metadata
        source = str(meta.get("source_file", ""))
        label = SOURCE_LABELS.get(source, source.replace(".txt", "") or "내부기술")
        section = str(meta.get("section_title", ""))
        selected = result.chunk_id in adopted_ids
        audits.append(
            CatalogCandidateAudit(
                catalog_id=result.chunk_id,
                solution_name=label,
                sku_label=f"{label} · {section[:36]}" if section else label,
                category_major=label,
                category_mid=section,
                category_sub=str(meta.get("chunk_index", "")),
                description=result.document[:240],
                similarity_score=_similarity_score(result),
                selected=selected,
                exclusion_reason=None if selected else "판정 근거에서 제외",
            )
        )
    return audits


def build_matched_skus(selected_sources: list[str], status: str) -> list[MatchedSolutionSku]:
    if status == "x":
        return []
    labels = kt_holdings_labels(selected_sources, status)
    if selected_sources:
        return [
            MatchedSolutionSku(
                catalog_id=source,
                solution_name=label,
                sku_label=label,
                category_major=label,
            )
            for source, label in zip(selected_sources, labels, strict=True)
        ]
    return []


def to_recommendation(
    requirement_id: str,
    result: InternalReviewResult,
    *,
    query: str = "",
) -> Recommendation:
    review = result.review
    ai_risk = _STATUS_TO_JUDGEMENT.get(review.status, Judgement.PARTIAL)
    holdings = kt_holdings_labels(result.selected_sources, review.status)
    missing = extract_missing_tech(review, query)

    return Recommendation(
        requirement_id=requirement_id,
        ai_risk=ai_risk,
        ai_reason=review.status_reason,
        related_solution=format_related_solution(review, result.selected_sources),
        missing_tech=missing,
        consortium_need=review.recommendation or None,
        matched_solutions=holdings,
        matched_solution_skus=build_matched_skus(result.selected_sources, review.status),
        rubric_scores={},
        catalog_audit=build_catalog_audit(result.evidence_results, review),
    )
