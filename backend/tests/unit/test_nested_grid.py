from __future__ import annotations

from prototype.v2.grid import _html_cell_text, _html_table_to_grid
from prototype.v2.korean_form import expand_detail_rows, extract_korean_form_document
from prototype.v2.extract import Req


def test_nested_table_preserved_in_cell() -> None:
    html = """
    <table>
    <tr><th>세부</th><th>
    <ul><li>◦ bullet one</li><li>◦ bullet two</li></ul>
    <table>
    <tr><th>분류</th><th>업무</th></tr>
    <tr><td>에이전트A</td><td>역할 설명</td></tr>
    </table>
    </th></tr>
    </table>
    """
    from bs4 import BeautifulSoup

    table = BeautifulSoup(html, "lxml").find("table")
    grid = _html_table_to_grid(table, 0)
    cell_text = grid.cells[0][1]
    assert "분류" in cell_text and "업무" in cell_text
    assert "에이전트A" in cell_text
    assert "bullet one" in cell_text


def test_expand_detail_bullets() -> None:
    from prototype.v2.korean_form import split_public_detail

    pieces = split_public_detail("◦ 첫번째 요건\n◦ 두번째 요건\n◦ 세번째 요건")
    assert len(pieces) == 3
    inline = split_public_detail("◦ 첫번째 ◦ 두번째 ◦ 세번째")
    assert len(inline) == 3
    r = Req(
        doc="t",
        table_id=1,
        page=None,
        rid="FUN-001",
        top="항목",
        mid="정의",
        detail="◦ 첫번째 요건\n◦ 두번째 요건\n◦ 세번째 요건",
        tab="FUN",
    )
    out = expand_detail_rows([r])
    assert len(out) == 3
    assert all(x.rid == "FUN-001" for x in out)
    assert all(x.top == "항목" for x in out)
    assert "첫번째" in out[0].detail
