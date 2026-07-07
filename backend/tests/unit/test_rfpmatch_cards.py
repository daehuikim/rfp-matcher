from __future__ import annotations

from prototype.rfpmatch.cards import build_rfp_cards
from prototype.rfpmatch.models import Section


def _section(title: str, level: int, html: str, page_idx: int = 1) -> Section:
    return Section(title=title, anchor=title, level=level, page_idx=page_idx, html=html, text=html)


def test_build_rfp_cards_assigns_part_section_category_by_level() -> None:
    sections = [
        _section("1장", 1, "<h1>1장</h1><p>개요</p>"),
        _section("1.1절", 2, "<p>세부 개요</p>"),
        _section("1.1.1항", 3, "<p>더 세부</p>"),
    ]
    cards = build_rfp_cards(sections)
    assert [c.requirement for c in cards] == ["1장", "1.1절", "1.1.1항"]
    assert cards[2].part == "1장"
    assert cards[2].section == "1.1절"
    assert cards[2].category == "1.1.1항"


def test_build_rfp_cards_resets_stack_on_sibling_level() -> None:
    sections = [
        _section("1장", 1, "<p>a</p>"),
        _section("1.1절", 2, "<p>b</p>"),
        _section("2장", 1, "<p>c</p>"),
    ]
    cards = build_rfp_cards(sections)
    assert cards[2].part == "2장"
    assert cards[2].section == ""


def test_build_rfp_cards_promotes_table_continuation_tail() -> None:
    sections = [
        _section("표 제목", 1, "<table><tr><td>구분</td><td>미완성 문장 없음</td></tr></table>"),
        _section("이어지는 내용", 1, "니다 이어지는 설명 텍스트"),
    ]
    cards = build_rfp_cards(sections)
    assert len(cards) == 2
