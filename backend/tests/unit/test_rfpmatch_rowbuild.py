from __future__ import annotations

from prototype.rfpmatch import rowbuild as rb
from prototype.rfpmatch.models import RfpCard


def test_split_atomic_detail_units_splits_korean_ordinal_bullets() -> None:
    units = rb._split_atomic_detail_units("가. 첫번째 항목입니다. 나. 두번째 항목입니다.")
    assert len(units) == 2
    assert units[0].startswith("가.")
    assert units[1].startswith("나.")


def test_extract_rows_from_table_card_two_column_with_th_header() -> None:
    card = RfpCard(
        card_id=1,
        requirement="요건1",
        html_excerpt=(
            "<table><tr><th>구분</th><th>상세내역</th></tr>"
            "<tr><td>보안</td><td>망분리 필요</td></tr></table>"
        ),
    )
    rows = rb._extract_rows_from_table_card(card, None, {})
    assert len(rows) == 1
    assert rows[0]["requirement"] == "보안"
    assert rows[0]["detail_requirement"] == "망분리 필요"
    assert rows[0]["table_rule_branch"] == "2단표"


def test_extract_rows_from_table_card_three_column() -> None:
    card = RfpCard(
        card_id=2,
        requirement="요건2",
        html_excerpt=(
            "<table><tr><th>항목</th><th>요구사항</th><th>상세</th></tr>"
            "<tr><td>보안</td><td>암호화</td><td>AES256 적용</td></tr></table>"
        ),
    )
    rows = rb._extract_rows_from_table_card(card, None, {})
    assert len(rows) == 1
    assert rows[0]["item_name"] == "보안"
    assert rows[0]["requirement"] == "암호화"
    assert rows[0]["detail_requirement"] == "AES256 적용"


def test_fallback_card_requirement_rows_returns_single_row_for_empty_card() -> None:
    card = RfpCard(card_id=3, requirement="요건3", html_excerpt="")
    rows = rb._fallback_card_requirement_rows(card, {})
    assert len(rows) == 1
    assert rows[0]["item_name"] == "요건3"


def test_fallback_table_card_rows_splits_pipe_free_cells() -> None:
    card = RfpCard(
        card_id=4,
        requirement="요건4",
        html_excerpt="<table><tr><td>A</td><td>B</td></tr></table>",
    )
    rows = rb._fallback_table_card_rows(card, {})
    assert rows


def test_row_requirement_id_prefix_uses_item_name_when_available() -> None:
    assert rb._row_requirement_id_prefix("망분리", "보안", "보안섹션", "FALLBACK") == "망분리"


def test_is_header_like_requirement_row_detects_repeated_header_terms() -> None:
    assert rb._is_header_like_requirement_row("항목", "요구사항", "상세내용") is True
    assert (
        rb._is_header_like_requirement_row("망분리", "물리적 망분리 적용", "AES256 암호화") is False
    )


def test_is_redundant_same_text_requirement_row_detects_triplicate() -> None:
    assert rb._is_redundant_same_text_requirement_row("동일문구", "동일문구", "동일문구") is True
    assert rb._is_redundant_same_text_requirement_row("항목", "요구사항", "상세") is False


def test_normalize_two_col_table_item_name_resolves_from_context() -> None:
    result = rb._normalize_two_col_table_item_name("보안 요구사항", fallback_title="정보보호")
    assert isinstance(result, str)
    assert result


def test_detail_dedup_key_text_preserves_leading_numbering() -> None:
    assert rb._detail_dedup_key_text("(9.3) 세부 항목") == "(9.3) 세부 항목"


def test_split_inline_standalone_bullet_units_splits_on_bullet_marker() -> None:
    units = rb._split_inline_standalone_bullet_units(
        "도입 목적은 다음과 같다 ▪ 업무 효율화 ▪ 비용 절감"
    )
    assert len(units) == 3


def test_split_inline_standalone_bullet_units_keeps_dash_term_chain_intact() -> None:
    # "A - B - C" 형태의 짧은 용어 연결은 하나의 원자 단위로 유지된다(분리하지 않음).
    units = rb._split_inline_standalone_bullet_units("서버 구성 - DB서버 - WAS서버")
    assert units == ["서버 구성 - DB서버 - WAS서버"]


def test_group_cards_by_section_preserves_first_seen_order() -> None:
    cards = [
        RfpCard(card_id=1, requirement="a", section="1장"),
        RfpCard(card_id=2, requirement="b", section="2장"),
        RfpCard(card_id=3, requirement="c", section="1장"),
    ]
    grouped = rb._group_cards_by_section(cards)
    assert [name for name, _ in grouped] == ["1장", "2장"]
    assert len(grouped[0][1]) == 2
