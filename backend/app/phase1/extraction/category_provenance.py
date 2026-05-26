from __future__ import annotations

from collections.abc import Sequence

from app.domain.enums import CategorySource
from app.domain.models import AtomicRow, ExtractionMetadata, Requirement
from app.phase1.extraction.atomization import AtomizationStrategy
from app.phase1.extraction.table_columns import REQ_CATEGORY_LABELS

UNKNOWN_GROUP = "미분류"
_FILLER_SUBCATEGORIES = frozenset({"기타", ""})


def category_column_header(headers: Sequence[str]) -> str | None:
    for h in headers:
        t = h.strip()
        if any(lbl in t for lbl in REQ_CATEGORY_LABELS):
            return t
    return None


def build_extraction_metadata(
    *,
    strategy: AtomizationStrategy,
    table_headers: Sequence[Sequence[str]],
) -> ExtractionMetadata:
    headers_flat: list[str] = []
    for row in table_headers:
        headers_flat.extend(row)
    col_header = category_column_header(headers_flat)
    has_col = col_header is not None and strategy == AtomizationStrategy.TABLE
    return ExtractionMetadata(
        atomization_strategy=strategy.value,
        has_requirement_category_column=has_col,
        category_column_header=col_header,
    )


def resolve_category_source(
    atom: AtomicRow,
    *,
    strategy: AtomizationStrategy,
    has_category_column: bool,
) -> CategorySource:
    if atom.section_index is not None or strategy == AtomizationStrategy.SECTION:
        return CategorySource.SECTION_HEADING
    if strategy == AtomizationStrategy.PARAGRAPH:
        return CategorySource.SYSTEM_INFERRED
    raw = (atom.category_raw or "").strip()
    if not has_category_column:
        return CategorySource.SECTION_HEADING if raw else CategorySource.SYSTEM_INFERRED
    if not raw or raw == UNKNOWN_GROUP:
        return CategorySource.SYSTEM_INFERRED
    return CategorySource.DOCUMENT_TABLE


def resolve_subcategory_source(subcategory: str | None) -> CategorySource | None:
    if not subcategory or subcategory in _FILLER_SUBCATEGORIES:
        return None
    return CategorySource.SYSTEM_INFERRED


def category_source_label(source: CategorySource) -> str:
    return {
        CategorySource.DOCUMENT_TABLE: "원문 조견표",
        CategorySource.SECTION_HEADING: "섹션 구조",
        CategorySource.SYSTEM_INFERRED: "시스템 추론",
    }[source]


def document_category_spec(
    meta: ExtractionMetadata | None,
    requirements: Sequence[Requirement] | None = None,
) -> str:
    if requirements and all(
        (r.category or "").strip() in ("", UNKNOWN_GROUP) for r in requirements
    ):
        return (
            "원문 조견표에서 「요건 구분」 값을 읽지 못했습니다. "
            "분류는 시스템 추론으로 부여되었습니다."
        )
    if meta is None:
        return "분류 출처 정보 없음"
    if meta.has_requirement_category_column:
        header = meta.category_column_header or "요건 구분"
        return f"조견표 「{header}」 열 기준 분류"
    if meta.atomization_strategy == AtomizationStrategy.SECTION.value:
        return (
            "원문에 「요건 구분」 조견표 열이 없습니다. "
            "분류는 섹션·중목차 구조에서 추출되었습니다."
        )
    if meta.atomization_strategy == AtomizationStrategy.PARAGRAPH.value:
        return (
            "원문에 「요건 구분」 조견표가 없습니다. "
            "분류는 본문 구조·시스템 추론으로 부여되었습니다."
        )
    return (
        "원문에 「요건 구분」 조견표 열이 없습니다. "
        "분류는 표 구조·시스템 추론으로 부여되었습니다."
    )


def summarize_category_sources(requirements: Sequence[Requirement]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for req in requirements:
        label = category_source_label(req.category_source)
        counts[label] = counts.get(label, 0) + 1
    return counts


def has_inferred_categories(requirements: Sequence[Requirement]) -> bool:
    return any(r.category_source != CategorySource.DOCUMENT_TABLE for r in requirements)


def subcategory_has_meaningful_data(requirements: Sequence[Requirement]) -> bool:
    for req in requirements:
        sub = (req.subcategory or "").strip()
        if sub and sub not in _FILLER_SUBCATEGORIES:
            return True
    return False
