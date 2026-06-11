"""문서 섹션 기반 탭 배정 — BC카드·신한 조견표 가독성."""
from __future__ import annotations

from prototype.v2.extract import Req, format_source
from prototype.v2.pipeline import _ordered_tabs
from prototype.v2.tab_naming import clean_tab_segment, coarse_tab_from_section


def _req(section: str, table_id: int = 0, page: int | None = None) -> Req:
    return Req(
        doc="BC",
        table_id=table_id,
        page=page,
        section_path=section,
        detail="요구 본문",
    )


def test_ordered_tabs_use_section_not_table_id() -> None:
    reqs = [
        _req("다. 인프라 공통 요구 사항", 6),
        _req("라. 서버 요구 사항", 7),
        _req("라. 서버 요구 사항", 7),
    ]
    out = _ordered_tabs(reqs)
    tabs = {r.tab for r in out}
    assert "표#6" not in tabs
    assert "표#7" not in tabs
    assert any("인프라" in t for t in tabs)
    assert any("서버" in t for t in tabs)
    assert len(tabs) == 2


def test_coarse_tab_strips_korean_enumerator() -> None:
    name = coarse_tab_from_section("II. > 다. 인프라 공통 요구 사항")
    assert "인프라" in name


def test_clean_tab_segment_strips_enumerator() -> None:
    assert "인프라" in clean_tab_segment("다. 인프라 공통 요구 사항")


def test_format_source_prefers_section_over_bare_table() -> None:
    s = format_source(table_id=6, page=None, section="다. 인프라 공통 요구 사항")
    assert "표#6" in s
    assert "인프라" in s
    assert s != "표#6"


def test_format_source_page_when_available() -> None:
    s = format_source(table_id=7, page=12, section="라. 서버 요구 사항")
    assert s == "p.12 · 표#7"
