from __future__ import annotations

from prototype.rfpmatch import toc


def test_extract_toc_from_html_with_leader_dots() -> None:
    html = (
        "<html><body>"
        "<h1>목차</h1>"
        "<p>1. 사업개요 ....... 3</p>"
        "<p>2. 사업범위 ....... 5</p>"
        "<h1>1. 사업개요</h1><p>본문</p>"
        "<h1>2. 사업범위</h1><p>본문</p>"
        "</body></html>"
    )
    items = toc.extract_toc(html)
    titles = [item.title for item in items]
    assert "사업개요" in titles
    assert "사업범위" in titles


def test_extract_sections_slices_by_heading() -> None:
    html = "<html><body><h1>1장</h1><p>첫 장 내용</p><h1>2장</h1><p>둘째 장 내용</p></body></html>"
    sections = toc.extract_sections(html, [])
    assert [s.title for s in sections] == ["1장", "2장"]
    assert "첫 장 내용" in sections[0].text
    assert "둘째 장 내용" in sections[1].text


def test_extract_sections_falls_back_to_toc_items_when_no_headings() -> None:
    html = "<html><body><p>표지만 있는 문서</p></body></html>"
    toc_items = [toc.TocItem(level=1, title="1장", anchor="1jang")]
    sections = toc.extract_sections(html, toc_items)
    assert len(sections) == 1
    assert sections[0].title == "1장"


def test_extract_toc_from_txt() -> None:
    txt = "목차\n1. 사업개요 ....... 3\n2. 사업범위 ....... 5\n"
    items = toc.extract_toc_from_txt(txt)
    assert {item.title for item in items} == {"사업개요", "사업범위"}


def test_detect_txt_toc_style_document_vs_ppt() -> None:
    document_style = "목차\n" + "\n".join(f"{i}. 항목 {i} ....... {i + 2}" for i in range(1, 10))
    ppt_style = "\n".join(f"슬라이드 제목 {i}" for i in range(1, 10))
    assert toc.detect_txt_toc_style(document_style) == "document"
    assert toc.detect_txt_toc_style(ppt_style) == "ppt"


def test_anchor_from_text_slugifies_and_marks_bullets() -> None:
    assert toc.anchor_from_text("사업 개요") == "사업-개요"
    assert toc.anchor_from_text("- 세부 항목", has_bullet_prefix=True).startswith("bullet-")


def test_extract_lines_from_tag_handles_list_and_table() -> None:
    from bs4 import BeautifulSoup

    ul = BeautifulSoup("<ul><li>가</li><li>나</li></ul>", "html.parser").ul
    assert toc.extract_lines_from_tag(ul) == ["가", "나"]

    table = BeautifulSoup("<table><tr><td>A</td><td>B</td></tr></table>", "html.parser").table
    assert toc.extract_lines_from_tag(table) == ["A B"]


def test_split_toc_body_html_by_toc_items() -> None:
    html = (
        "<html><body>"
        "<h1>목차</h1><p>1. 사업개요 ....... 3</p>"
        "<h1>1. 사업개요</h1><p>본문 내용입니다</p>"
        "</body></html>"
    )
    items = toc.extract_toc(html)
    toc_html, body_html = toc.split_toc_body_html_by_toc_items(html, items)
    assert "본문 내용입니다" in body_html
