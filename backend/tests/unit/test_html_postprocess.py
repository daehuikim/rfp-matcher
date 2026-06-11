from __future__ import annotations

from pathlib import Path

from app.phase1.converters.html_postprocess import compact_html, postprocess_html_file, raw_html_sibling


def test_compact_strips_style_and_font() -> None:
    raw = """<!DOCTYPE html><html><head>
    <style>p{color:red}</style><meta name="x" content="y"/>
    </head><body>
    <p style="font-size:12pt"><font face="Arial">요구사항 본문</font></p>
    <table><tr><td style="border:1px">A</td><td>B</td></tr></table>
    </body></html>"""
    out = compact_html(raw)
    assert "style=" not in out
    assert "<font" not in out
    assert "요구사항 본문" in out
    assert "<table>" in out
    assert "A" in out and "B" in out


def test_postprocess_html_file_in_place(tmp_path: Path) -> None:
    p = tmp_path / "t.html"
    p.write_text(
        "<html><body><p style='x'>hello</p><table><tr><td>1</td></tr></table></body></html>",
        encoding="utf-8",
    )
    tables, _ = postprocess_html_file(p)
    text = p.read_text()
    assert tables == 1
    assert "style=" not in text
    assert "hello" in text
    raw = raw_html_sibling(p)
    assert raw.is_file()
    assert "style=" in raw.read_text()
