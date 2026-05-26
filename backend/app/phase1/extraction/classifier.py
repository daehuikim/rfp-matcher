from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import Counter

from pydantic import BaseModel, Field

from app.domain.models import AtomicRow
from app.llm.base import AsyncLlmClient, Message

logger = logging.getLogger(__name__)


class Classifier(ABC):
    """
    atomic row → category 매핑 추상.

    두 가지 모드(PassThrough / LlmAdaptive)가 같은 출력 형태로 수렴해서
    상위 서비스가 분기 없이 사용한다.
    """

    @abstractmethod
    async def classify(self, atoms: list[AtomicRow]) -> list[str]:
        """입력 atoms 순서를 유지하며 각 항목의 category 라벨을 반환."""


class PassThroughClassifier(Classifier):
    """
    명시 분류(`atom.category_raw`)가 있는 RFP용 — 줄바꿈만 정리해서 그대로 사용.

    적용 조건은 호출 측의 `select_classifier`에서 판단한다.
    """

    async def classify(self, atoms: list[AtomicRow]) -> list[str]:
        return [_normalize(a.category_raw) or "기타" for a in atoms]


class _LlmSchema(BaseModel):
    schema_categories: list[str] = Field(min_length=1)
    assignments: list[str] = Field(
        description="입력 atoms와 동일 순서·동일 길이로, 각 항목의 분류명",
        min_length=1,
    )


class LlmAdaptiveClassifier(Classifier):
    """
    명시 분류가 없는 RFP(하나은행 류)에서 LLM이 분류 스키마를 즉석에서 생성하고
    각 atomic을 그 스키마에 할당한다.

    - 한 번의 structured_output 호출에 모든 atomic을 묶어 보냄 (atomic 수가 많을 땐 청킹).
    - 청킹시 첫 청크에서 스키마 확정 → 후속 청크는 같은 스키마로 할당만.
    """

    def __init__(self, llm: AsyncLlmClient, chunk_size: int = 40) -> None:
        self._llm = llm
        self._chunk = chunk_size

    async def classify(self, atoms: list[AtomicRow]) -> list[str]:
        if not atoms:
            return []
        chunks = [atoms[i : i + self._chunk] for i in range(0, len(atoms), self._chunk)]
        first = await self._classify_chunk(chunks[0], schema=None)
        schema = first.schema_categories
        out: list[str] = list(first.assignments[: len(chunks[0])])
        for ch in chunks[1:]:
            nxt = await self._classify_chunk(ch, schema=schema)
            out.extend(nxt.assignments[: len(ch)])
        # 안전망: 누락된 라벨은 "기타"
        return [a if a and a.strip() else "기타" for a in out]

    async def _classify_chunk(self, atoms: list[AtomicRow], schema: list[str] | None) -> _LlmSchema:
        lines = "\n".join(f"{i}: {a.text}" for i, a in enumerate(atoms))
        if schema is None:
            instr = (
                "다음 atomic 요구사항 목록을 보고 자연스러운 분류 스키마(3~8개)를 만들고, "
                "각 atomic이 어느 분류에 속하는지 동일 순서·동일 길이로 반환하라."
            )
        else:
            instr = (
                "다음 atomic 요구사항들을 아래 사전 정의된 분류 스키마에 할당하라. "
                "스키마를 바꾸지 말 것.\n"
                f"스키마: {schema}"
            )
        prompt = (
            f"{instr}\n\n"
            "[atomic 목록]\n"
            f"{lines}\n\n"
            "응답 JSON: "
            '{"schema_categories": ["...", ...], '
            '"assignments": ["분류명0", "분류명1", ...]}'
        )
        out = await self._llm.structured_output(
            [Message(role="user", content=prompt)],
            _LlmSchema,
        )
        if len(out.assignments) < len(atoms):
            # LLM이 일부만 응답한 경우 — "기타"로 패딩
            out.assignments.extend(["기타"] * (len(atoms) - len(out.assignments)))
        return out


def select_classifier(
    atoms: list[AtomicRow],
    llm: AsyncLlmClient,
    *,
    coverage_floor: float = 0.6,
    diversity_floor: int = 1,
) -> Classifier:
    """
    명시 분류 충분 → PassThrough, 아니면 LlmAdaptive.

    기준:
      - coverage: category_raw가 채워진 atom 비율 ≥ floor
      - diversity: 정규화된 category_raw 종류 수 ≥ floor
    """
    if not atoms:
        return PassThroughClassifier()
    raws = [_normalize(a.category_raw) for a in atoms]
    filled = [r for r in raws if r]
    coverage = len(filled) / len(atoms)
    diversity = len(set(filled))
    logger.info(
        "classifier select: coverage=%.2f diversity=%d (n=%d)",
        coverage,
        diversity,
        len(atoms),
    )
    if coverage >= coverage_floor and diversity >= diversity_floor:
        return PassThroughClassifier()
    return LlmAdaptiveClassifier(llm)


def _normalize(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.split())


def category_histogram(categories: list[str]) -> dict[str, int]:
    """디버깅/리포트용 — category 카운트."""
    return dict(Counter(categories))
