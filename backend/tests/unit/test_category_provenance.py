from __future__ import annotations

from app.domain.enums import CategorySource
from app.domain.models import AtomicRow, ExtractionMetadata, Requirement
from app.phase1.extraction.atomization import AtomizationStrategy
from app.phase1.extraction.category_provenance import (
    build_extraction_metadata,
    document_category_spec,
    resolve_category_source,
    resolve_subcategory_source,
    subcategory_has_meaningful_data,
)


def test_build_extraction_metadata_table_with_category_column() -> None:
    meta = build_extraction_metadata(
        strategy=AtomizationStrategy.TABLE,
        table_headers=[["요건 구분", "상세내용"]],
    )
    assert meta.has_requirement_category_column is True
    assert meta.category_column_header == "요건 구분"


def test_build_extraction_metadata_section_without_column() -> None:
    meta = build_extraction_metadata(
        strategy=AtomizationStrategy.SECTION,
        table_headers=[["구분", "주요 역할"]],
    )
    assert meta.has_requirement_category_column is False


def test_resolve_category_source_section() -> None:
    atom = AtomicRow(
        doc_id="d",
        table_index=None,
        source_cell="x",
        bullet_marker=None,
        text="detail",
        row_seq=0,
        category_raw="가. 보안",
        section_index=1,
    )
    source = resolve_category_source(
        atom,
        strategy=AtomizationStrategy.SECTION,
        has_category_column=False,
    )
    assert source == CategorySource.SECTION_HEADING


def test_resolve_category_source_table_column() -> None:
    atom = AtomicRow(
        doc_id="d",
        table_index=0,
        source_cell="x",
        bullet_marker=None,
        text="detail",
        row_seq=0,
        category_raw="데이터 수집",
    )
    source = resolve_category_source(
        atom,
        strategy=AtomizationStrategy.TABLE,
        has_category_column=True,
    )
    assert source == CategorySource.DOCUMENT_TABLE


def test_subcategory_ignores_filler_기타() -> None:
    reqs = [
        Requirement(
            id="r1",
            doc_id="d",
            category="가. 보안",
            subcategory="기타",
            code="c1",
            name="n",
            detail="d",
        )
    ]
    assert subcategory_has_meaningful_data(reqs) is False


def test_document_spec_for_section_strategy() -> None:
    meta = ExtractionMetadata(atomization_strategy="section", has_requirement_category_column=False)
    spec = document_category_spec(meta)
    assert "요건 구분" in spec
    assert "섹션" in spec


def test_subcategory_source_is_inferred() -> None:
    assert resolve_subcategory_source("모니터링") == CategorySource.SYSTEM_INFERRED
    assert resolve_subcategory_source("기타") is None
