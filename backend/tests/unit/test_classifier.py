from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.domain.models import AtomicRow
from app.llm.base import Message
from app.llm.fake_client import FakeLlmClient
from app.phase1.extraction.classifier import (
    LlmAdaptiveClassifier,
    PassThroughClassifier,
    select_classifier,
)


def _atom(text: str, cat: str | None = None) -> AtomicRow:
    return AtomicRow(
        doc_id="d",
        table_index=0,
        source_cell=text,
        bullet_marker=None,
        text=text,
        row_seq=0,
        category_raw=cat,
    )


@pytest.mark.asyncio
async def test_passthrough_uses_normalized_category_raw() -> None:
    atoms = [
        _atom("a", " 데이터  수집 \n"),
        _atom("b", "데이터\n수집"),
        _atom("c", None),
    ]
    out = await PassThroughClassifier().classify(atoms)
    assert out == ["데이터 수집", "데이터 수집", "기타"]


@pytest.mark.asyncio
async def test_select_picks_passthrough_when_coverage_high() -> None:
    atoms = [
        _atom("a", "데이터 수집"),
        _atom("b", "데이터 수집"),
        _atom("c", "저장 구조"),
        _atom("d", "저장 구조"),
    ]
    c = select_classifier(atoms, FakeLlmClient())
    assert isinstance(c, PassThroughClassifier)


@pytest.mark.asyncio
async def test_select_picks_llm_when_no_explicit_categories() -> None:
    atoms = [_atom(f"req-{i}", None) for i in range(5)]
    c = select_classifier(atoms, FakeLlmClient())
    assert isinstance(c, LlmAdaptiveClassifier)


@pytest.mark.asyncio
async def test_select_picks_llm_when_coverage_too_low() -> None:
    # 10건 중 2건만 category_raw — coverage 0.2 < 기본 floor 0.6
    atoms = [_atom(f"req-{i}", "데이터" if i < 2 else None) for i in range(10)]
    c = select_classifier(atoms, FakeLlmClient())
    assert isinstance(c, LlmAdaptiveClassifier)


@pytest.mark.asyncio
async def test_llm_adaptive_returns_assignments_in_order() -> None:
    atoms = [_atom(f"req-{i}", None) for i in range(4)]

    def handler(schema: type[BaseModel], _msgs: list[Message]) -> BaseModel:
        return schema.model_validate(
            {
                "schema_categories": ["수집", "저장", "보안"],
                "assignments": ["수집", "저장", "보안", "저장"],
            }
        )

    out = await LlmAdaptiveClassifier(FakeLlmClient(structured_handler=handler)).classify(atoms)
    assert out == ["수집", "저장", "보안", "저장"]


@pytest.mark.asyncio
async def test_llm_adaptive_pads_missing_assignments_with_기타() -> None:
    atoms = [_atom(f"req-{i}", None) for i in range(5)]

    def handler(schema: type[BaseModel], _msgs: list[Message]) -> BaseModel:
        return schema.model_validate(
            {
                "schema_categories": ["수집"],
                "assignments": ["수집", "수집"],  # 5 중 2개만 응답
            }
        )

    out = await LlmAdaptiveClassifier(FakeLlmClient(structured_handler=handler)).classify(atoms)
    assert out == ["수집", "수집", "기타", "기타", "기타"]
