from __future__ import annotations

from app.services.extraction import _resolve_category_labels


def test_resolve_category_promotes_truncation_fix() -> None:
    category, sub, sub_src = _resolve_category_labels("니터링", "모니터링")
    assert category == "모니터링"
    assert sub is None
    assert sub_src is None


def test_resolve_category_keeps_same_label() -> None:
    category, sub, _ = _resolve_category_labels("데이터 수집", "데이터 수집")
    assert category == "데이터 수집"
    assert sub is None


def test_resolve_category_keeps_raw_when_canon_is_기타() -> None:
    category, sub, _ = _resolve_category_labels("비정형 데이터 플랫폼 구축", "기타")
    assert category == "비정형 데이터 플랫폼 구축"
    assert sub is None
