from __future__ import annotations

from prototype.rfpmatch import html_tables as ht


def _span_block(text: str, *, block_type: str = "text", page_idx: int = 0) -> dict:
    return {
        "type": block_type,
        "page_idx": page_idx,
        "lines": [{"spans": [{"content": text}]}],
    }


def test_build_html_document_renders_text_blocks() -> None:
    html = ht.build_html_document([_span_block("사업 개요입니다")], None)
    assert "사업 개요입니다" in html
    assert "<html>" in html


def test_merge_consecutive_tables_in_html_merges_repeated_header_continuation() -> None:
    # 페이지 경계로 잘린 표: 다음 조각의 첫 행이 이전 표 첫 행과 동일(반복 헤더)하면 병합.
    html = (
        "<html><body>"
        "<table><tr><td>구분</td><td>내용</td></tr><tr><td>A</td><td>1</td></tr></table>"
        "<table><tr><td>구분</td><td>내용</td></tr><tr><td>B</td><td>2</td></tr></table>"
        "</body></html>"
    )
    merged = ht.merge_consecutive_tables_in_html(html)
    assert merged.count("<table") == 1
    assert "A" in merged and "B" in merged


def test_merge_consecutive_tables_in_html_raw_stitches_adjacent_tables() -> None:
    html = (
        "<html><body>"
        "<table><tr><td>A</td><td>1</td></tr></table>"
        "<table><tr><td>B</td><td>2</td></tr></table>"
        "</body></html>"
    )
    merged = ht.merge_consecutive_tables_in_html_raw(html)
    assert merged.count("<table") == 1


def test_promote_standalone_two_col_fragment_html_synthesizes_table() -> None:
    html = "<div><p>구분: 세부 항목입니다 상세 내용을 담고 있는 문장입니다.</p></div>"
    promoted = ht.promote_standalone_two_col_fragment_html(html, title="테스트")
    assert isinstance(promoted, str)


def test_merge_empty_cells_upward_in_html_fills_blank_cell_from_below() -> None:
    html = (
        "<html><body><table>"
        "<tr><td></td><td>x</td></tr>"
        "<tr><td>값</td><td>y</td></tr>"
        "</table></body></html>"
    )
    result = ht.merge_empty_cells_upward_in_html(html)
    assert "값" in result
