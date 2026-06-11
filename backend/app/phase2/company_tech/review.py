from __future__ import annotations

from app.llm.base import AsyncLlmClient, Message
from app.phase2.company_tech.models import SearchResult, TechnicalReview

_REVIEW_SYSTEM = (
    "당신은 회사 내부 기술 근거를 바탕으로 사용자 요청 기술의 처리 가능성을 판정하는 AI 기술 검토자입니다.\n"
    "반드시 제공된 내부 근거만 사용하세요. 근거에 없는 기능, 성능, 구축 경험, 운영 역량, 호환성을 추측하거나 만들어내지 마세요.\n"
    "\n"
    "판정은 대분류명, 제품명, 솔루션명, 단순 키워드 유사도가 아니라 기술적 적합성을 기준으로 해야 합니다.\n"
    "먼저 사용자 요청의 핵심 기술 요구사항을 1개 이상으로 분해하세요.\n"
    "각 핵심 요구사항별로 검색된 내부 근거가 직접 지원하는지, 부분적으로만 관련되는지, 근거가 없는지 구분하세요.\n"
    "최종 판정은 가장 많이 등장한 키워드가 아니라 핵심 요구사항의 직접 지원 여부를 기준으로 결정하세요.\n"
    "\n"
    "검색된 내부 근거가 상위 수준의 일반 역량만 설명하는 경우, 이를 세부 기술 조건을 직접 지원하는 근거로 확대 해석하지 마세요.\n"
    "보수적으로 판단하세요. 질의가 내부 근거와 무관하거나, 너무 모호하거나, 키워드만 약하게 겹치면 x를 선택하세요.\n"
    "직접적인 내부 근거가 없으면 기본적으로 x를 선택하세요.\n"
    "\n"
    "판정 기준:\n"
    "- circle: 검색된 내부 근거가 사용자 요청의 핵심 기술 요구사항 대부분을 직접 지원하고, 중요한 기술적 공백이 없을 때만 선택하세요.\n"
    "- triangle: 검색된 내부 근거가 사용자 요청의 핵심 목적과 같은 기술 도메인에 있으며, 핵심 요구사항 중 의미 있는 일부를 직접 지원하지만, "
    "다른 핵심 요구사항, 적용 범위, 연동, 성능, 호환성, 운영 준비도 근거가 불명확할 때만 선택하세요.\n"
    "- x: 근거가 없거나, 일반적 설명뿐이거나, 무관하거나, 키워드만 유사하거나, 핵심 요구사항을 기술적으로 직접 지원하지 못하면 선택하세요.\n"
    "\n"
    "status_reason은 검색된 내부 근거를 기반으로 판정의 기술적 이유를 한국어로 설명하세요.\n"
    "missing_tech에는 이번 요구사항을 내부 근거로도 채우지 못한 **핵심 기술 갭**만 0~2개 적으세요. "
    "3개를 채우려 하지 말고, 정말 중요한 것만 남기세요. 확실한 갭이 없으면 빈 배열 []을 반환하세요. "
    "요구 본문에 이미 있는 단어(AX, 보안, 로드맵 등)를 그대로 반복하지 마세요. "
    "요구와 무관한 카탈로그·제품명, 단일 일반어(Red, Teaming), 근거 설명에만 등장하는 용어는 넣지 마세요. "
    "gaps와 unsupported_items는 missing_tech와 같은 목적이며, 가능하면 missing_tech에만 적어도 됩니다.\n"
    "referenced_sources에는 circle 또는 triangle일 때 판정 근거로 직접 사용한 chunk_id를 1개 이상 반드시 넣으세요. "
    "제공된 근거 블록의 [chunk_id] 문자열(예: intelligence-studio.txt:1)을 그대로 사용하고, "
    "chunk_index 숫자(0, 1)만 단독으로 넣지 마세요. x일 때는 비워두세요.\n"
    'JSON 형식: {"status":"circle|triangle|x","status_reason":"...","matched_capabilities":[],"unsupported_items":[],'
    '"strengths":[],"gaps":[],"missing_tech":[],"recommendation":"...","referenced_sources":[]}'
)


def build_review_context(results: list[SearchResult], max_chars_per_chunk: int = 1200) -> str:
    blocks: list[str] = []
    for result in results:
        metadata = result.metadata
        topics = metadata.get("topics") or []
        keywords = metadata.get("keywords") or []
        if isinstance(topics, list):
            topics = ", ".join(str(t) for t in topics)
        if isinstance(keywords, list):
            keywords = ", ".join(str(k) for k in keywords)

        source = metadata.get("source_file", "-")
        chunk_index = metadata.get("chunk_index", "-")
        section = metadata.get("section_title", "")
        retrieval: list[str] = []
        if result.vector_rank is not None:
            retrieval.append(f"vector_rank={result.vector_rank}")
        if result.bm25_rank is not None:
            retrieval.append(f"bm25_rank={result.bm25_rank}")

        blocks.append(
            "\n".join(
                [
                    f"[{result.chunk_id}]",
                    f"source: {source}",
                    f"chunk_index: {chunk_index}",
                    f"section: {section}",
                    f"topics: {topics}",
                    f"keywords: {keywords}",
                    f"retrieval: {', '.join(retrieval)}",
                    "content:",
                    result.document[:max_chars_per_chunk],
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)


def normalize_referenced_sources(
    refs: list[str],
    evidence_results: list[SearchResult],
) -> list[str]:
    """LLM이 chunk_index(0, 1)만 반환하는 경우 실제 chunk_id로 정규화."""
    if not refs:
        return []

    chunk_ids = {result.chunk_id for result in evidence_results}
    index_to_ids: dict[str, list[str]] = {}
    for result in evidence_results:
        chunk_index = result.metadata.get("chunk_index")
        if chunk_index is not None:
            index_to_ids.setdefault(str(chunk_index), []).append(result.chunk_id)

    normalized: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        text = ref.strip()
        if not text or text in seen:
            continue

        if text in chunk_ids:
            normalized.append(text)
            seen.add(text)
            continue

        if text.isdigit():
            matches = index_to_ids.get(text, [])
            if len(matches) == 1:
                chunk_id = matches[0]
                if chunk_id not in seen:
                    normalized.append(chunk_id)
                    seen.add(chunk_id)
                continue
            if len(matches) > 1:
                for chunk_id in matches:
                    if chunk_id not in seen:
                        normalized.append(chunk_id)
                        seen.add(chunk_id)
                continue

        normalized.append(text)
        seen.add(text)
    return normalized


async def generate_technical_review(
    llm: AsyncLlmClient,
    model: str,
    query: str,
    selected_sources: list[str],
    evidence_results: list[SearchResult],
) -> TechnicalReview:
    user_content = (
        f"User requested technology:\n{query}\n\n"
        f"Selected source files:\n{', '.join(selected_sources) if selected_sources else '전체 검색'}\n\n"
        f"Retrieved internal evidence:\n{build_review_context(evidence_results)}"
    )
    parsed = await llm.structured_output(
        [
            Message(role="system", content=_REVIEW_SYSTEM),
            Message(role="user", content=user_content),
        ],
        TechnicalReview,
        model=model,
        purpose="company_tech:review",
        max_tokens=2048,
    )
    parsed.strengths = [item.strip() for item in parsed.strengths if item.strip()]
    parsed.gaps = [item.strip() for item in parsed.gaps if item.strip()]
    parsed.missing_tech = [item.strip() for item in parsed.missing_tech if item.strip()]
    parsed.matched_capabilities = [item.strip() for item in parsed.matched_capabilities if item.strip()]
    parsed.unsupported_items = [item.strip() for item in parsed.unsupported_items if item.strip()]
    parsed.referenced_sources = normalize_referenced_sources(
        parsed.referenced_sources,
        evidence_results,
    )
    return parsed
