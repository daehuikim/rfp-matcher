from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import DocumentMime, Judgement, PipelineStage


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Document(BaseModel):
    model_config = ConfigDict(frozen=False)

    id: str
    src_path: Path
    mime: DocumentMime
    pages: int | None = None
    uploaded_at: datetime = Field(default_factory=_utcnow)


class HtmlDoc(BaseModel):
    doc_id: str
    html_path: Path
    table_count: int = 0
    paragraph_count: int = 0


class TableRef(BaseModel):
    """HTML 내 조견표의 위치 참조 — header 매칭 + LLM 검증 결과."""

    doc_id: str
    table_index: int  # html 내 table 태그 순번
    header_columns: list[str]
    confidence: float = 0.0
    located_via: str = "heuristic"  # "heuristic" | "llm" | "fallback-paragraph"
    category_col_index: int | None = None
    detail_col_index: int | None = None


class AtomicRow(BaseModel):
    """조견표 한 셀에서 ①②③/볼렛 단위로 분해된 한 줄."""

    doc_id: str
    table_index: int | None  # None: paragraph 분기
    source_cell: str  # 원본 셀(또는 단락) 텍스트
    bullet_marker: str | None  # "①", "•", "-", "1)" 등
    text: str  # atomic 텍스트
    row_seq: int  # 같은 셀 내 일련번호 (0-based)
    category_raw: str | None = None  # 원본 좌측 분류 라벨 (예: "데이터 수집")


class Requirement(BaseModel):
    id: str
    doc_id: str
    category: str  # adaptive 분류 (SFR/DAR/... 또는 동적)
    code: str  # 예: "SFR-004" or "데이터수집-1"
    name: str
    definition: str | None = None
    detail: str
    deliverables: str | None = None
    related: list[str] = Field(default_factory=list)
    source_atomic_id: str | None = None  # AtomicRow 추적용


class CatalogCandidateAudit(BaseModel):
    """카탈로그 탐색 top-k 후보 — UI에서 채택/제외·유사도·사유 표시."""

    catalog_id: str
    solution_name: str
    category_major: str = ""
    similarity_score: float = 0.0
    selected: bool = False
    exclusion_reason: str | None = None


class Recommendation(BaseModel):
    requirement_id: str
    ai_risk: Judgement
    ai_reason: str
    missing_tech: list[str] = Field(default_factory=list)
    consortium_need: str | None = None
    matched_solutions: list[str] = Field(default_factory=list)  # 솔루션명 top-k
    rubric_scores: dict[str, float] = Field(default_factory=dict)
    catalog_audit: list[CatalogCandidateAudit] = Field(default_factory=list)


class HumanJudgement(BaseModel):
    requirement_id: str
    mark: Judgement = Judgement.UNSET
    note: str = ""
    updated_at: datetime = Field(default_factory=_utcnow)


class PipelineEvent(BaseModel):
    doc_id: str
    stage: PipelineStage
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=_utcnow)
    error: str | None = None
