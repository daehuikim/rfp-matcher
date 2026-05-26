from __future__ import annotations

import pytest

from app.domain.models import AtomicRow
from app.llm.fake_client import FakeLlmClient
from app.phase1.extraction.classifier import PassThroughClassifier, select_classifier


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
async def test_passthrough_returns_normalized_raw_only() -> None:
    atoms = [
        _atom("a", " 데이터  수집 \n"),
        _atom("b", "데이터\n수집"),
        _atom("c", None),
    ]
    out = await PassThroughClassifier().classify(atoms)
    assert out == ["데이터 수집", "데이터 수집", ""]


@pytest.mark.asyncio
async def test_select_always_passthrough() -> None:
    atoms = [_atom(f"req-{i}", None) for i in range(5)]
    c = select_classifier(atoms, FakeLlmClient())
    assert isinstance(c, PassThroughClassifier)
    out = await c.classify(atoms)
    assert out == [""] * 5
