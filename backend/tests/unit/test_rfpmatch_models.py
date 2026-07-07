from __future__ import annotations

from prototype.rfpmatch.models import RfpCard, Section, TocItem


def test_toc_item_defaults() -> None:
    item = TocItem(level=1, title="사업 개요", anchor="sec-1")
    assert item.page_idx is None
    assert item.page_estimate is None


def test_section_holds_html_and_text() -> None:
    section = Section(
        title="1장", anchor="sec-1", level=1, page_idx=3, html="<p>본문</p>", text="본문"
    )
    assert section.level == 1
    assert section.page_idx == 3


def test_rfp_card_defaults_are_blank_not_none() -> None:
    card = RfpCard(card_id=1, requirement="요구사항 A")
    assert card.part == ""
    assert card.section == ""
    assert card.html_excerpt == ""
    assert card.anchor is None
