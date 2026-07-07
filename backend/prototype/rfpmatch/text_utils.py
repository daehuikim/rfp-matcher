"""텍스트/셀 정규화 공용 유틸 — sections/partition/rowbuild/requirement_table이 공유.

rfpmatch/step456_shared.py에서 여러 모듈이 함께 쓰는 순수 문자열/셀-텍스트 헬퍼만 뽑았다.
(step456_shared.py 자체는 8,831줄 단일 파일이라 이 저장소 컨벤션에 맞게 역할별로 쪼갠다.)
"""

from __future__ import annotations

import re
from html import escape

from bs4 import BeautifulSoup

from .toc import extract_lines_from_tag as _extract_lines_from_tag
from .toc import text_with_real_linebreaks as _text_with_real_linebreaks


def _cell_text_preserve_breaks(cell) -> str:
    text = _text_with_real_linebreaks(BeautifulSoup(str(cell), "html.parser"))
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = [re.sub(r"[ \t\f\v]+", " ", line).rstrip() for line in text.split("\n")]
    lines: list[str] = []
    previous_blank = True
    for line in raw_lines:
        if line.strip():
            lines.append(line)
            previous_blank = False
            continue
        if not previous_blank:
            lines.append("")
        previous_blank = True
    return "\n".join(lines).strip("\n")


def _cell_text_compact(cell) -> str:
    text = BeautifulSoup(str(cell), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _cell_text_without_nested_tables(cell, preserve_breaks: bool = False) -> str:
    cloned = BeautifulSoup(str(cell), "html.parser")
    for nested in cloned.find_all("table"):
        nested.decompose()
    if preserve_breaks:
        text = _text_with_real_linebreaks(cloned)
        if not text:
            return ""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        raw_lines = [re.sub(r"[ \t\f\v]+", " ", line).rstrip() for line in text.split("\n")]
        lines: list[str] = []
        previous_blank = True
        for line in raw_lines:
            if line.strip():
                lines.append(line)
                previous_blank = False
                continue
            if not previous_blank:
                lines.append("")
            previous_blank = True
        return "\n".join(lines).strip("\n")
    text = cloned.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _tag_without_nested_tables(tag) -> BeautifulSoup:
    cloned = BeautifulSoup(str(tag), "html.parser")
    root = cloned.find(getattr(tag, "name", None)) or cloned
    for nested in root.find_all("table"):
        nested.decompose()
    return cloned


def _is_heading_like_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return False
    if re.match(
        r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+)\s*",
        compact,
        flags=re.IGNORECASE,
    ):
        return True
    return bool(re.match(r"^[가나다라마바사아자차카타파하]\.\s*", compact))


def _split_embedded_heading_suffixes(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return []
    match = re.search(
        r"^(?P<prefix>.+?[\.?!])\s+(?P<heading>(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s+.+)$",
        compact,
        flags=re.IGNORECASE,
    )
    if not match:
        return [compact]
    prefix = match.group("prefix").strip()
    heading = match.group("heading").strip()
    if not prefix or not heading or not _is_heading_like_text(heading):
        return [compact]
    return [prefix, heading]


def _merge_quoted_numeric_reference_spans(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""

    # 따옴표/인용부호 안의 "1.\n2." 같은 문서 참조는 제목 분해가 아니라
    # 본문 내 인용으로 보아 줄바꿈을 공백으로만 정규화한다.
    def _repl(match: re.Match) -> str:
        inner = _normalize_requirement_text(match.group("inner") or "")
        inner = re.sub(r"\n+", " ", inner).strip()
        return f"『{inner}』"

    normalized = re.sub(
        r"『(?P<inner>\s*\d+\.\s*\n\s*\d+\.[^』]*?)』",
        _repl,
        normalized,
        flags=re.DOTALL,
    )

    def _repl_bracket(match: re.Match) -> str:
        inner = _normalize_requirement_text(match.group("inner") or "")
        inner = re.sub(r"\n+", " ", inner).strip()
        return f"[{inner}]"

    return re.sub(
        r"\[(?P<inner>\s*\d+\.\s*\n\s*\d+\.[^\]]*?)\]",
        _repl_bracket,
        normalized,
        flags=re.DOTALL,
    )


def _parse_style_indentation(style: str | None) -> float | None:
    if not style:
        return None
    text = str(style).strip()
    if not text:
        return None
    matches = re.findall(
        r"(margin-left|padding-left|text-indent)\s*:\s*([-+]?\d*\.?\d+)\s*(px|pt|em|rem)?",
        text,
        flags=re.IGNORECASE,
    )
    values: list[float] = []
    for _, raw_value, unit in matches:
        try:
            value = float(raw_value)
        except ValueError:
            continue
        unit = (unit or "px").lower()
        if unit == "pt":
            value *= 1.333
        elif unit in {"em", "rem"}:
            value *= 16.0
        if value < 0:
            value = abs(value)
        values.append(value)
    return max(values) if values else None


def _extract_block_indentation_px(tag) -> float | None:
    values: list[float] = []
    current = tag
    for _ in range(3):
        if current is None or not hasattr(current, "get"):
            break
        style = current.get("style")
        if style:
            value = _parse_style_indentation(style)
            if value is not None:
                values.append(value)
        current = getattr(current, "parent", None)
    return max(values) if values else None


def _extract_block_page_idx(tag) -> int | None:
    current = tag
    for _ in range(4):
        if current is None or not hasattr(current, "get"):
            break
        page_attr = current.get("data-page")
        if page_attr not in (None, "", "None"):
            try:
                return int(page_attr)
            except (TypeError, ValueError):
                match = re.search(r"\d+", str(page_attr))
                if match:
                    try:
                        return int(match.group(0))
                    except ValueError:
                        pass
        current = getattr(current, "parent", None)
    return None


def _normalize_requirement_text(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    compact: list[str] = []
    previous_blank = True
    for line in lines:
        if line:
            compact.append(line)
            previous_blank = False
            continue
        if not previous_blank:
            compact.append("")
        previous_blank = True
    return "\n".join(compact).strip()


def _normalize_ocr_bullet_markers(value: str) -> str:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return ""
    lines: list[str] = []
    for line in normalized.split("\n"):
        if re.match(r"^[sS]\s+(?=\S)", line):
            line = re.sub(r"^[sS]\s+", "• ", line, count=1)
        lines.append(line)
    return "\n".join(lines).strip()


def _is_title_like_requirement_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    stripped = re.sub(
        r"^\s*(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+[\.\)]?)\s*",
        "",
        normalized,
        flags=re.IGNORECASE,
    ).strip()
    if not stripped:
        return False
    title_keywords = (
        "사업명",
        "구축방향",
        "요청사항",
        "요구사항",
        "유의사항",
        "작성 방법",
        "작성 방안",
        "작성 요령",
        "사업 개요",
        "사업범위",
        "제안 범위",
        "제안요청",
        "목차",
        "개요",
        "배경",
        "목적",
        "효과",
        "일정",
        "관리",
        "보안",
        "표준",
        "계획",
        "방안",
        "구성",
    )
    compact = re.sub(r"\s+", "", stripped)
    if any(keyword in stripped for keyword in title_keywords):
        return True
    if len(stripped) <= 24 and len(stripped.split()) <= 4:
        return True
    return compact.endswith(
        (
            "사업명",
            "구축방향",
            "요청사항",
            "요구사항",
            "유의사항",
            "사업개요",
            "제안범위",
            "정보보호요구사항",
            "기술요건",
            "보안요건",
        )
    )


def _body_text_blocks(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    blocks: list[dict] = []
    candidates = body.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "p",
            "li",
            "table",
            "div",
            "section",
            "article",
            "figcaption",
        ],
        recursive=True,
    )
    for tag in candidates:
        if tag.name == "table":
            if tag.find_parent("table") is not None:
                continue
        else:
            if (
                tag.find_parent(
                    ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "figcaption"]
                )
                is not None
            ):
                continue
            if tag.find_parent("table") is not None:
                continue
        if tag.name in {"div", "section", "article"} and tag.find(
            ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"], recursive=True
        ):
            continue
        if tag.name == "table":
            html_chunk = str(tag)
            text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        else:
            text_soup = _tag_without_nested_tables(tag)
            html_chunk = str(text_soup.find(tag.name) or text_soup)
            text = re.sub(r"\s+", " ", text_soup.get_text(" ", strip=True)).strip()
        if not text:
            continue
        text = _normalize_ocr_bullet_markers(text)
        indent_px = _extract_block_indentation_px(tag)
        page_idx = _extract_block_page_idx(tag)
        if tag.name == "table":
            blocks.append(
                {
                    "html": html_chunk,
                    "text": text,
                    "tag": tag.name,
                    "indent_px": indent_px,
                    "page_idx": page_idx,
                }
            )
            continue

        if tag.name in {"h1", "h2", "h3", "h4", "h5", "h6", "figcaption"}:
            blocks.append(
                {
                    "html": html_chunk,
                    "text": text,
                    "tag": tag.name,
                    "indent_px": indent_px,
                    "page_idx": page_idx,
                }
            )
            continue

        line_source = _tag_without_nested_tables(tag).find(tag.name) or tag
        lines = [line for line in _extract_lines_from_tag(line_source) if line.strip()]
        if len(lines) > 1:
            for line in lines:
                for part in _split_embedded_heading_suffixes(line):
                    line_html = f"<{tag.name}>{escape(part)}</{tag.name}>"
                    blocks.append(
                        {
                            "html": line_html,
                            "text": part,
                            "tag": tag.name,
                            "indent_px": indent_px,
                            "page_idx": page_idx,
                        }
                    )
            continue

        split_parts = _split_embedded_heading_suffixes(text)
        if len(split_parts) > 1:
            for part in split_parts:
                part_html = f"<{tag.name}>{escape(part)}</{tag.name}>"
                blocks.append(
                    {
                        "html": part_html,
                        "text": part,
                        "tag": tag.name,
                        "indent_px": indent_px,
                        "page_idx": page_idx,
                    }
                )
            continue

        blocks.append(
            {
                "html": html_chunk,
                "text": text,
                "tag": tag.name,
                "indent_px": indent_px,
                "page_idx": page_idx,
            }
        )
    return _coalesce_fragmented_word_blocks(blocks)


_FRAGMENT_BULLET_MARKER_RE = re.compile(
    r"^(?:[□○◇◆▪■▶※•·ㆍ\-–—]|\d+[\.\)]|[가나다라마바사아자차카타파하][\.\)]|[IVXLCDM]+[\.\)])$",
    flags=re.IGNORECASE,
)


def _is_fragment_block(block: dict) -> bool:
    text = str(block.get("text") or "").strip()
    if not text or re.search(r"[.!?]$", text):
        return False
    return " " not in text or len(text) <= 8


def _coalesce_fragmented_word_blocks(blocks: list[dict]) -> list[dict]:
    """단어 단위로 쪼개진 <li>/<p> 연속 구간을 불릿 경계로 재결합.

    opendataloader가 일부 PDF(예: 실측된 JB금융 RFP)에서 한 문장을 단어마다 별도
    <li>로 뱉는 경우가 있다 — 이런 문서는 블록당 한 줄을 가정하는 이후 처리
    (제목 매칭·아토마이즈)가 전부 단어 단위로 쪼개져 무너진다. 같은 tag/page의
    초단문(공백 없거나 8자 이하) 블록이 4개 이상 이어지면 그 구간만 □/○/-/숫자./
    가. 같은 불릿·번호 마커를 새 줄의 시작으로 보고 재결합한다. 정상적으로 변환된
    문서는 이런 런이 거의 생기지 않아 사실상 no-op이다.
    """
    result: list[dict] = []
    i = 0
    n = len(blocks)
    while i < n:
        block = blocks[i]
        if block.get("tag") not in {"li", "p"} or not _is_fragment_block(block):
            result.append(block)
            i += 1
            continue
        j = i
        tag = block.get("tag")
        page_idx = block.get("page_idx")
        while (
            j < n
            and blocks[j].get("tag") == tag
            and blocks[j].get("page_idx") == page_idx
            and _is_fragment_block(blocks[j])
        ):
            j += 1
        run = blocks[i:j]
        if len(run) < 4:
            result.extend(run)
            i = j
            continue
        groups: list[list[dict]] = []
        for item in run:
            item_text = str(item.get("text") or "").strip()
            if not groups or _FRAGMENT_BULLET_MARKER_RE.match(item_text):
                groups.append([item])
            else:
                groups[-1].append(item)
        for group in groups:
            merged_text = " ".join(str(g.get("text") or "").strip() for g in group).strip()
            merged_html = f"<{tag}>{escape(merged_text)}</{tag}>"
            result.append(
                {
                    "html": merged_html,
                    "text": merged_text,
                    "tag": tag,
                    "indent_px": group[0].get("indent_px"),
                    "page_idx": page_idx,
                }
            )
        i = j
    return result


def _plain_text_from_html_excerpt(html_excerpt: str) -> str:
    if not html_excerpt:
        return ""
    soup = BeautifulSoup(html_excerpt, "html.parser")
    return _normalize_ocr_bullet_markers(_text_with_real_linebreaks(soup))


def _html_excerpt_lines(html_excerpt: str, *, include_tables: bool = False) -> list[str]:
    if not html_excerpt:
        return []
    blocks = _body_text_blocks(html_excerpt)
    if not blocks:
        return []
    lines: list[str] = []
    for block in blocks:
        if not include_tables and block.get("tag") == "table":
            continue
        block_html = str(block.get("html") or "")
        if not block_html:
            continue
        block_soup = BeautifulSoup(block_html, "html.parser")
        block_tag = block_soup.find(str(block.get("tag") or "")) or block_soup
        for raw_line in _extract_lines_from_tag(block_tag):
            text = _normalize_requirement_text(raw_line)
            if text:
                lines.append(text)
    return lines
