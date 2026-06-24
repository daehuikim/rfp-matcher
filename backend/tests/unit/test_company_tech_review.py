from __future__ import annotations

from app.phase2.company_tech.models import TechnicalReview
from app.phase2.company_tech.review import normalize_referenced_sources
from app.phase2.company_tech.models import SearchResult


def test_technical_review_coerces_numeric_referenced_sources() -> None:
    review = TechnicalReview.model_validate(
        {
            "status": "circle",
            "status_reason": "지원 가능",
            "referenced_sources": [1, 0],
        }
    )
    assert review.referenced_sources == ["1", "0"]


def test_normalize_referenced_sources_maps_chunk_index() -> None:
    evidence = [
        SearchResult(
            chunk_id="intelligence-studio.txt:1",
            document="chunk one",
            metadata={"source_file": "intelligence-studio.txt", "chunk_index": 1},
        ),
        SearchResult(
            chunk_id="intelligence-studio.txt:0",
            document="chunk zero",
            metadata={"source_file": "intelligence-studio.txt", "chunk_index": 0},
        ),
    ]
    normalized = normalize_referenced_sources(["1", "0"], evidence)
    assert normalized == ["intelligence-studio.txt:1", "intelligence-studio.txt:0"]
