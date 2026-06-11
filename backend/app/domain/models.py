from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import CategorySource, DocumentMime, Judgement, PipelineStage


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ExtractionMetadata(BaseModel):
    """문서 단위 추출·분류 명세 — UI·Excel 개요용."""

    atomization_strategy: str = "table"  # table | section | paragraph
    has_requirement_category_column: bool = False
    category_column_header: str | None = None


class Document(BaseModel):
    model_config = ConfigDict(frozen=False)

    id: str
    src_path: Path
    mime: DocumentMime
    pages: int | None = None
    content_hash: str | None = None  # 원본 파일 SHA256 — 아티팩트 캐시 키
    source_filename: str | None = None  # 업로드·샘플 원본 파일명 (예: 하나.pdf)
    display_name: str | None = None  # 사용자 지정 프로젝트명 (없으면 source_filename stem)
    extraction_meta: ExtractionMetadata | None = None
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
    has_category_column: bool = False  # 헤더에 「요건 구분」 등 명시 열 존재
    source_page: int | None = None  # PDF 원본 페이지 (1-based)
    section_heading: str | None = None  # 직전 섹션 제목 (예: 2.2 제안 요구사항 상세)


class SectionRef(BaseModel):
    """HTML 본문 섹션(대목차) — 조견표 대신 '3. 프로젝트 범위' 등 텍스트 블록."""

    doc_id: str
    section_index: int
    heading: str
    level: int = 1  # 1=3./Ⅱ., 2=가. (중목차는 atomizer 내부 category)
    block_start: int  # BLOCK 순서 index (inclusive)
    block_end: int  # exclusive
    located_via: str = "section_heuristic"
    confidence: float = 0.0


class AtomicRow(BaseModel):
    """조견표 한 셀에서 ①②③/볼렛 단위로 분해된 한 줄."""

    doc_id: str
    table_index: int | None  # None: paragraph/section 분기
    source_cell: str  # 원본 셀(또는 단락) 텍스트
    bullet_marker: str | None  # "①", "•", "-", "1)" 등
    text: str  # atomic 텍스트
    row_seq: int  # 같은 셀 내 일련번호 (0-based)
    category_raw: str | None = None  # 원본 좌측 분류 라벨 (예: "데이터 수집")
    section_index: int | None = None  # SectionRef.section_index
    source_page: int | None = None
    source_section: str | None = None


class Requirement(BaseModel):
    id: str
    doc_id: str
    category: str  # 조견표 원본 「요건 구분」(carry-forward) 또는 구조·추론 분류
    subcategory: str | None = None  # canonicalize 소분류 태그
    category_source: CategorySource = CategorySource.DOCUMENT_TABLE
    subcategory_source: CategorySource | None = None
    code: str  # 예: "SFR-004" or "데이터수집-1"
    name: str
    definition: str | None = None
    detail: str
    deliverables: str | None = None
    related: list[str] = Field(default_factory=list)
    source_atomic_id: str | None = None  # AtomicRow 추적용
    source_page: int | None = None  # PDF 원본 페이지 (1-based)
    source_section: str | None = None  # 섹션 제목 (예: 2.2 제안 요구사항 상세)
    source_table_index: int | None = None  # HTML table 순번
    source_ref: str | None = None  # V2 출처 (예: p.18 · 표#633)
    detail_images: list[str] = Field(default_factory=list)  # [표]·화면(안) PNG — repo 상대 경로
    # 원문 추출이 아니라 LLM 이 생성한 값을 가진 export 칼럼 key 집합
    # (예: {"code","name","subcategory"}) — Excel 에서 주황색 셀로 구분 표시.
    ai_generated_fields: set[str] = Field(default_factory=set)


class CatalogCandidateAudit(BaseModel):
    """카탈로그 탐색 top-k 후보 — UI에서 채택/제외·유사도·사유 표시."""

    catalog_id: str
    solution_name: str
    sku_label: str = ""  # 솔루션명 · 소분류 — 동일 브랜드 SKU 구분용
    category_major: str = ""
    category_mid: str = ""
    category_sub: str = ""
    description: str = ""
    similarity_score: float = 0.0
    selected: bool = False
    exclusion_reason: str | None = None


class MatchedSolutionSku(BaseModel):
    """LLM이 채택한 카탈로그 SKU — 브랜드명만으로는 구분 불가."""

    catalog_id: str
    solution_name: str
    sku_label: str = ""
    category_major: str = ""
    category_mid: str = ""
    category_sub: str = ""
    description: str = ""


class Recommendation(BaseModel):
    requirement_id: str
    ai_risk: Judgement
    ai_reason: str
    missing_tech: list[str] = Field(default_factory=list)
    consortium_need: str | None = None
    matched_solutions: list[str] = Field(default_factory=list)  # SKU 라벨 (하위 호환)
    matched_solution_skus: list[MatchedSolutionSku] = Field(default_factory=list)
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
