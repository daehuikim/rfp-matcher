from __future__ import annotations

from prototype.rfpmatch import toc_normalize as tn
from prototype.rfpmatch.models import TocItem


def _item(
    title: str, level: int = 1, page_idx: int | None = None, page_estimate: int | None = None
) -> TocItem:
    return TocItem(
        level=level,
        title=title,
        anchor=tn.slugify(title),
        page_idx=page_idx,
        page_estimate=page_estimate,
    )


def test_fill_missing_pages_from_neighbors_interpolates() -> None:
    items = [
        _item("1. 개요", page_idx=2),
        _item("2. 범위", page_idx=None),
        _item("3. 일정", page_idx=8),
    ]
    filled = tn.fill_missing_pages_from_neighbors(items)
    assert [i.page_idx for i in filled] == [2, 2, 8]


def test_reconcile_pages_from_candidates_fills_from_matching_title() -> None:
    items = [_item("1. 개요", page_idx=None)]
    candidates = [_item("1. 개요", page_idx=3)]
    fixed = tn.reconcile_pages_from_candidates(items, candidates)
    assert fixed[0].page_idx == 3


def test_relevel_items_infers_level_from_title_when_out_of_range() -> None:
    bad = TocItem(level=9, title="1.2.3 세부항목", anchor="x")
    fixed = tn.relevel_items([bad])
    assert fixed[0].level == 3


def test_dedup_toc_items_by_title_drops_duplicates_and_unsequenced_bullets() -> None:
    items = [_item("1. 개요"), _item("1. 개요"), _item("- 참고")]
    deduped = tn.dedup_toc_items_by_title(items)
    assert [i.title for i in deduped] == ["1. 개요"]


def test_drop_toc_heading_items_removes_literal_toc_titles() -> None:
    items = [_item("목차"), _item("1. 개요")]
    kept = tn.drop_toc_heading_items(items)
    assert [i.title for i in kept] == ["1. 개요"]


def test_find_missing_numbering_sequences_detects_gap() -> None:
    items = [_item("1.1 항목"), _item("1.3 항목")]
    gaps = tn.find_missing_numbering_sequences(items)
    assert gaps == ["1.2"]


def test_move_toc_item_reorders() -> None:
    items = [_item("A"), _item("B"), _item("C")]
    moved = tn.move_toc_item(items, 2, -1)
    assert [i.title for i in moved] == ["A", "C", "B"]


def test_insert_and_append_toc_item() -> None:
    items = [_item("A")]
    inserted = tn.insert_toc_item(items, 0, title="새 항목")
    assert inserted[0].title == "새 항목"
    appended = tn.append_toc_item(items, title="끝 항목")
    assert appended[-1].title == "끝 항목"
