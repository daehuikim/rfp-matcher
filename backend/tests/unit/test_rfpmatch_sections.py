from __future__ import annotations

from prototype.rfpmatch.models import TocItem
from prototype.rfpmatch.sections import build_cards_from_sections, build_sections_from_final_toc

HTML = """
<html><body>
<h1>1. 사업개요</h1><p>본 사업은 시스템 고도화를 목적으로 한다.</p>
<h1>2. 사업범위</h1><p>범위 세부내용입니다.</p>
<h2>2.1 상세범위</h2><p>더 세부적인 범위 설명입니다.</p>
</body></html>
"""

TOC_ITEMS = [
    TocItem(level=1, title="1. 사업개요", anchor="1-사업개요", page_idx=1),
    TocItem(level=1, title="2. 사업범위", anchor="2-사업범위", page_idx=2),
    TocItem(level=2, title="2.1 상세범위", anchor="2-1-상세범위", page_idx=3),
]


def test_build_sections_from_final_toc_slices_by_matched_heading() -> None:
    sections, match_debug = build_sections_from_final_toc(HTML, TOC_ITEMS)
    assert [s.title for s in sections] == ["1. 사업개요", "2. 사업범위", "2.1 상세범위"]
    assert "시스템 고도화" in sections[0].text
    assert "더 세부적인" in sections[2].text
    assert all(entry["matched"] for entry in match_debug)


def test_build_sections_from_final_toc_falls_back_to_empty_section_when_unmatched() -> None:
    toc_items = [
        *TOC_ITEMS,
        TocItem(level=1, title="3. 존재하지 않는 섹션", anchor="none", page_idx=9),
    ]
    sections, match_debug = build_sections_from_final_toc(HTML, toc_items)
    assert sections[-1].title == "3. 존재하지 않는 섹션"
    assert sections[-1].text == ""
    assert match_debug[-1]["matched"] is False


def test_build_sections_from_final_toc_fills_missing_anchor_via_slugify() -> None:
    toc_items = [TocItem(level=1, title="1. 사업개요", anchor="", page_idx=1)]
    sections, _ = build_sections_from_final_toc(HTML, toc_items)
    assert sections[0].anchor  # 원본의 미정의 _slugify 버그가 고쳐졌는지 확인


def test_build_cards_from_sections_assigns_hierarchy_by_level() -> None:
    sections, _ = build_sections_from_final_toc(HTML, TOC_ITEMS)
    cards = build_cards_from_sections(sections)
    assert [c.requirement for c in cards] == ["1. 사업개요", "2. 사업범위", "2.1 상세범위"]
    assert cards[2].part == "2. 사업범위"
    assert cards[2].section == "2.1 상세범위"
