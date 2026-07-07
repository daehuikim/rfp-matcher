from __future__ import annotations

from prototype.rfpmatch import text_utils as tu


def test_body_text_blocks_splits_headings_body_and_tables() -> None:
    html = (
        "<html><body>"
        "<h1>1장</h1><p>본문 내용</p>"
        "<table><tr><td>a</td><td>b</td></tr></table>"
        "</body></html>"
    )
    blocks = tu._body_text_blocks(html)
    assert [(b["tag"], b["text"]) for b in blocks] == [
        ("h1", "1장"),
        ("p", "본문 내용"),
        ("table", "a b"),
    ]


def test_plain_text_from_html_excerpt_strips_tags_preserves_lines() -> None:
    text = tu._plain_text_from_html_excerpt("<p>안녕 <b>하세요</b></p><p>둘째 줄</p>")
    assert "안녕" in text
    assert "하세요" in text
    assert "둘째 줄" in text


def test_html_excerpt_lines_flattens_list_items() -> None:
    lines = tu._html_excerpt_lines("<ul><li>가나다</li><li>라마바</li></ul>")
    assert lines == ["가나다", "라마바"]


def test_plain_text_from_html_excerpt_joins_per_word_spans_within_one_heading() -> None:
    # opendataloader가 굵기별 <span>으로 한 줄을 단어마다 쪼개는 경우(JB금융 실측) —
    # 인라인 span 경계는 줄바꿈이 아니라 그대로 이어붙어야 한다.
    excerpt = (
        "<h6><span>1.</span><span> </span><span>프로젝트</span>"
        "<span> </span><span>개요</span></h6><li>□ 사업 기간 : 1개월</li>"
    )
    text = tu._plain_text_from_html_excerpt(excerpt)
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines == ["1. 프로젝트 개요", "□ 사업 기간 : 1개월"]


def test_cell_text_preserve_breaks_keeps_list_items_separate_but_joins_word_spans() -> None:
    from bs4 import BeautifulSoup

    html = (
        "<td><ul>"
        "<li><p><span>●</span><span> </span><span>구축형</span>"
        "<span> </span><span>기반</span></p></li>"
        "<li><p><span>●</span><span> </span><span>AI</span>"
        "<span> </span><span>기술</span></p></li>"
        "</ul></td>"
    )
    cell = BeautifulSoup(html, "html.parser").td
    text = tu._cell_text_preserve_breaks(cell)
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines == ["● 구축형 기반", "● AI 기술"]


def test_is_title_like_requirement_text_detects_keyword_and_short_phrase() -> None:
    assert tu._is_title_like_requirement_text("1. 사업 개요") is True
    long_sentence_without_title_keyword = (
        "제안업체는 반드시 별첨된 서식에 맞춰 세부 견적 내역을 작성하여 제출하여야 한다."
    )
    assert tu._is_title_like_requirement_text(long_sentence_without_title_keyword) is False


def test_is_heading_like_text_detects_numbered_and_hangul_ordinal_prefixes() -> None:
    assert tu._is_heading_like_text("1. 사업개요") is True
    assert tu._is_heading_like_text("가. 세부내용") is True
    assert tu._is_heading_like_text("일반 본문 문장입니다") is False


def test_normalize_requirement_text_collapses_blank_line_runs() -> None:
    assert tu._normalize_requirement_text("a\n\n\n\nb") == "a\n\nb"


def test_normalize_ocr_bullet_markers_converts_stray_s_prefix() -> None:
    assert tu._normalize_ocr_bullet_markers("s 항목 하나").startswith("• 항목")


def test_split_embedded_heading_suffixes_splits_sentence_then_heading() -> None:
    parts = tu._split_embedded_heading_suffixes("본문이 끝난다. 2. 다음 섹션 제목")
    assert len(parts) == 2
    assert parts[1].startswith("2.")


def test_cell_text_helpers_extract_and_compact() -> None:
    from bs4 import BeautifulSoup

    cell = BeautifulSoup("<td>  여러   공백   포함  </td>", "html.parser").td
    assert tu._cell_text_compact(cell) == "여러 공백 포함"


def test_extract_block_page_idx_reads_data_page_attribute() -> None:
    from bs4 import BeautifulSoup

    tag = BeautifulSoup('<p data-page="3">내용</p>', "html.parser").p
    assert tu._extract_block_page_idx(tag) == 3


def test_body_text_blocks_coalesces_word_per_li_fragments() -> None:
    # opendataloader가 일부 PDF에서 한 줄을 단어 단위 <li>로 쪼개 뱉는 경우(JB금융 실측) 재현.
    html = (
        "<html><body><ul>"
        "<li>□</li><li>대상</li><li>계열사</li><li>:</li><li>JB금융지주,</li>"
        "<li>전북은행,</li><li>광주은행,</li><li>JB우리캐피탈</li>"
        "<li>□</li><li>구축</li><li>방식</li>"
        "</ul></body></html>"
    )
    blocks = tu._body_text_blocks(html)
    texts = [b["text"] for b in blocks]
    assert texts == [
        "□ 대상 계열사 : JB금융지주, 전북은행, 광주은행, JB우리캐피탈",
        "□ 구축 방식",
    ]


def test_body_text_blocks_leaves_short_normal_list_untouched() -> None:
    # 짧은 문서에 우연히 나온 몇 개의 짧은 리스트 항목은(4개 미만) 병합 대상이 아니다.
    html = "<html><body><ul><li>가.</li><li>나.</li><li>다.</li></ul></body></html>"
    blocks = tu._body_text_blocks(html)
    assert [b["text"] for b in blocks] == ["가.", "나.", "다."]
