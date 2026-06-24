from __future__ import annotations

from app.llm.base import AsyncLlmClient, Message
from app.phase2.company_tech.constants import CATEGORY_HINTS
from app.phase2.company_tech.models import QueryRouting

_ROUTING_SYSTEM = (
    "당신은 회사의 기술 벡터 DB에서 사용자의 질의를 하나 이상의 소스 파일로 라우팅하는 역할을 합니다.\n"
    "반드시 제공된 소스 파일명 목록에서만 선택하세요.\n"
    "질의가 여러 카테고리에 걸쳐 있으면 여러 파일을 반환하세요.\n"
    "어떤 카테고리도 명확하게 관련되지 않은 경우에만 selected_sources를 빈 배열로 반환하세요.\n"
    "존재하지 않는 소스 파일명을 만들어내지 마세요.\n"
    'JSON 형식: {"selected_sources": ["rag.txt"], "reasoning": "한국어 설명"}'
)


def build_category_prompt(source_files: list[str]) -> str:
    lines = []
    for source_file in source_files:
        hint = CATEGORY_HINTS.get(source_file, source_file)
        lines.append(f"- {source_file}: {hint}")
    return "\n".join(lines)


async def classify_query_sources(
    llm: AsyncLlmClient,
    model: str,
    query: str,
    source_files: list[str],
) -> QueryRouting:
    user_content = (
        f"Available source files:\n{build_category_prompt(source_files)}\n\n"
        f"User query:\n{query}"
    )
    parsed = await llm.structured_output(
        [
            Message(role="system", content=_ROUTING_SYSTEM),
            Message(role="user", content=user_content),
        ],
        QueryRouting,
        model=model,
        purpose="company_tech:routing",
    )
    allowed = set(source_files)
    selected: list[str] = []
    seen: set[str] = set()
    for source_file in parsed.selected_sources:
        if source_file in allowed and source_file not in seen:
            selected.append(source_file)
            seen.add(source_file)
    parsed.selected_sources = selected
    parsed.reasoning = " ".join(parsed.reasoning.split())
    return parsed
