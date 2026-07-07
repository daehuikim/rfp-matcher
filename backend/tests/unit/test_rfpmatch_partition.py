from __future__ import annotations

from bs4 import BeautifulSoup

from prototype.rfpmatch.models import RfpCard
from prototype.rfpmatch.partition import (
    _detect_bullet_level,
    _is_header_only_split_table_html,
    _partition_card_for_requirement_build,
    _partition_card_into_table_body_segments,
    _partition_table_cards_by_columns,
    _split_table_rows_by_boundary_value,
    _table_has_span,
    _table_visual_matrix,
)


def test_partition_card_into_table_body_segments_splits_by_tag_kind() -> None:
    card = RfpCard(
        card_id=1,
        card_no="1",
        requirement="요건1",
        html_excerpt=(
            "<p>선행 설명입니다.</p>"
            "<table><tr><td>a</td><td>b</td></tr></table>"
            "<p>후속 설명입니다.</p>"
        ),
    )
    segments = _partition_card_into_table_body_segments(card)
    kinds = [s.sub_subject for s in segments]
    assert "표" in kinds
    assert "본문" in kinds
    assert all(s.card_no.startswith("1-") for s in segments)


def test_partition_card_into_table_body_segments_returns_clone_when_no_html() -> None:
    card = RfpCard(card_id=1, card_no="1", requirement="요건1", html_excerpt="")
    segments = _partition_card_into_table_body_segments(card)
    assert len(segments) == 1
    assert segments[0].requirement == "요건1"


def test_partition_card_for_requirement_build_returns_original_when_only_table() -> None:
    card = RfpCard(card_id=1, requirement="요건", html_excerpt="<table><tr><td>a</td></tr></table>")
    result = _partition_card_for_requirement_build(card)
    assert result == [card]


def test_partition_table_cards_by_columns_groups_by_boundary_value() -> None:
    card = RfpCard(
        card_id=2,
        card_no="2",
        requirement="요건 목록",
        html_excerpt=(
            "<table><tr><td>A</td><td>1</td></tr><tr><td>A</td><td>2</td></tr>"
            "<tr><td>B</td><td>3</td></tr></table>"
        ),
    )
    split = _partition_table_cards_by_columns(card)
    assert [s.card_no for s in split] == ["2-t1", "2-t2"]


def test_partition_table_cards_by_columns_skips_excluded_keyword_titles() -> None:
    card = RfpCard(
        card_id=3,
        card_no="3",
        requirement="제출 서식 안내",
        html_excerpt="<table><tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></table>",
    )
    split = _partition_table_cards_by_columns(card)
    assert len(split) == 1
    assert split[0].card_no == "3"


def test_table_visual_matrix_expands_rowspan() -> None:
    table = BeautifulSoup(
        "<table><tr><td rowspan='2'>x</td><td>y</td></tr><tr><td>z</td></tr></table>", "html.parser"
    ).table
    assert _table_visual_matrix(table) == [["x", "y"], ["x", "z"]]


def test_table_has_span_detects_rowspan_colspan() -> None:
    plain = BeautifulSoup("<table><tr><td>a</td></tr></table>", "html.parser").table
    spanned = BeautifulSoup("<table><tr><td colspan='2'>a</td></tr></table>", "html.parser").table
    assert _table_has_span(plain) is False
    assert _table_has_span(spanned) is True


def test_is_header_only_split_table_html_detects_repeated_header_rows() -> None:
    html = "<table><tr><td>구분</td><td>내용</td></tr><tr><td>구분</td><td>내용</td></tr></table>"
    assert _is_header_only_split_table_html(html) is True


def test_split_table_rows_by_boundary_value_groups_consecutive_same_key() -> None:
    # 경계 열은 "모든 행이 채워진 마지막(가장 오른쪽) 열" — 2열이 일부 비어 있어
    # 0열("구분")로 결정됨.
    matrix = [["구분", "내용"], ["A", "1"], ["A", ""], ["B", "3"]]
    groups = _split_table_rows_by_boundary_value(matrix)
    assert groups == [[1, 2, 3], [1, 4]]


def test_detect_bullet_level_ranks_korean_and_numeric_markers() -> None:
    assert _detect_bullet_level("가. 항목") == 2
    assert _detect_bullet_level("1.1 세부항목") == 3
    assert _detect_bullet_level("일반 문장입니다") is None
