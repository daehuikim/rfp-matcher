from __future__ import annotations

from prototype.v3.cell_chunker import chunk_html


def test_bullet_list_in_cell() -> None:
    html = """<html><body><table><tr><td><ul>
    <li><p>- 시스템 모니터링: Ontune</p></li>
    <li><p>- WAS 모니터링: Jenifer</p></li>
    </ul></td></tr></table></body></html>"""
    r = chunk_html(html)
    bullets = [u for u in r.units if u.kind == "bullet"]
    assert len(bullets) == 2
    assert "Ontune" in bullets[0].text


def test_nested_table_detected() -> None:
    html = """<html><body><table><tr><td>
    <p>4.11. 산출물 관리 방안</p>
    <table><tr><td>inner</td></tr></table>
    <ul><li><p>가. 산출물별 표준 양식</p></li></ul>
    </td></tr></table></body></html>"""
    r = chunk_html(html)
    assert r.nested_tables >= 1
    assert any(u.kind == "bullet" for u in r.units)
