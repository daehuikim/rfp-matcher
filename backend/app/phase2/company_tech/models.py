from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class QueryRouting(BaseModel):
    selected_sources: list[str] = Field(default_factory=list)
    reasoning: str = ""


def _coerce_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out


class TechnicalReview(BaseModel):
    status: Literal["circle", "triangle", "x"]
    status_reason: str
    matched_capabilities: list[str] = Field(default_factory=list)
    unsupported_items: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    missing_tech: list[str] = Field(default_factory=list)
    recommendation: str = ""
    referenced_sources: list[str] = Field(default_factory=list)

    @field_validator(
        "matched_capabilities",
        "unsupported_items",
        "strengths",
        "gaps",
        "missing_tech",
        "referenced_sources",
        mode="before",
    )
    @classmethod
    def _coerce_string_list_fields(cls, value: object) -> list[str]:
        return _coerce_str_list(value)


@dataclass
class ChunkRecord:
    id: str
    document: str
    metadata: dict


@dataclass
class SearchResult:
    chunk_id: str
    document: str
    metadata: dict
    score: float = 0.0
    vector_rank: int | None = None
    vector_distance: float | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None


@dataclass
class InternalReviewResult:
    review: TechnicalReview
    routing: QueryRouting
    selected_sources: list[str]
    evidence_results: list[SearchResult]
