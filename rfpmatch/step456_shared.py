from __future__ import annotations

from contextlib import nullcontext
from collections import Counter
import hashlib
import io
import json
import re
import zipfile
from html import escape
from pathlib import Path
from typing import Callable

import streamlit as st
from bs4 import BeautifulSoup

from rfpmatch.app_state import load_openai_api_key
from rfpmatch.shared_io import current_artifact_root, render_safe_table
from rfpmatch.shared_rfp_cards import build_rfp_cards
from rfpmatch.shared_models import RfpCard, Section, TocItem
from rfpmatch.toc_parser import _anchor_from_text, _extract_lines_from_tag, _split_document_regions


_SECTION_REQUIREMENT_CACHE_VERSION = "2026-06-24-1"

_COMMON_INLINE_BODY_START_MARKERS = (
    "본 사업은",
    "본 사업의",
    "해당 사업은",
    "해당 사업의",
    "본 프로젝트는",
    "본 프로젝트의",
    "본 프로젝트와 관련한",
    "해당 프로젝트는",
    "해당 프로젝트의",
    "프로젝트는",
    "프로젝트의",
    "사업은",
    "사업의",
    "과업은",
    "과업의",
    "제안업체의 ",
    "제안업체는 ",
    "제안사의 ",
    "제안사는 ",
    "수행사의 ",
    "수행사는 ",
    "납품 솔루션은 ",
    "당사는 ",
    "다음과 같은 관점으로",
    "다음의 내용을 포함하여",
    "다음과 같이 기술합니다",
    "구축 완료 이후",
    "추후 ",
)


def _cell_text_preserve_breaks(cell) -> str:
    text = BeautifulSoup(str(cell), "html.parser").get_text("\n", strip=False)
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
        text = cloned.get_text("\n", strip=False)
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


def _cards_to_workbook_bytes(cards: list[RfpCard]) -> bytes:
    headers = ["card_id", "card_no", "requirement", "subject", "category", "sub_subject", "part", "section", "page_idx", "anchor", "html_excerpt"]
    rows = [headers] + [
        [
            card.card_id,
            card.card_no,
            card.requirement,
            card.subject,
            getattr(card, "category", None),
            card.sub_subject,
            getattr(card, "part", None) or "",
            card.section,
            card.page_idx,
            card.anchor,
            card.html_excerpt,
        ]
        for card in cards
    ]

    def col_name(idx: int) -> str:
        name = ""
        while idx:
            idx, rem = divmod(idx - 1, 26)
            name = chr(65 + rem) + name
        return name

    def cell_xml(value: object, ref: str, bold: bool = False) -> str:
        text = "" if value is None else str(value)
        text = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        style = ' s="1"' if bold else ""
        return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{text}</t></is></c>'

    sheet_rows: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row, start=1):
            ref = f"{col_name(c_idx)}{r_idx}"
            cells.append(cell_xml(value, ref, bold=(r_idx == 1)))
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheetData>"
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="RFP Cards" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/>'
        '</cellXfs></styleSheet>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>'
        ))
        zf.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'
        ))
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>'
        ))
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr("xl/styles.xml", styles_xml)
    return buffer.getvalue()


def _clear_section_requirement_cache() -> None:
    st.session_state["cards_step2_section_requirement_tables"] = None
    st.session_state["cards_step2_section_requirement_debug"] = None
    st.session_state["cards_step2_section_requirement_usage"] = None
    st.session_state["cards_step2_section_requirement_signature"] = None


def _section_requirement_cards_signature(cards: list[RfpCard]) -> str:
    payload = {
        "version": _SECTION_REQUIREMENT_CACHE_VERSION,
        "cards": [
            {
                "card_no": getattr(card, "card_no", None),
                "requirement": getattr(card, "requirement", None),
                "subject": getattr(card, "subject", None),
                "sub_subject": getattr(card, "sub_subject", None),
                "part": getattr(card, "part", None),
                "section": getattr(card, "section", None),
                "page_idx": getattr(card, "page_idx", None),
                "anchor": getattr(card, "anchor", None),
                "html_excerpt": getattr(card, "html_excerpt", None),
            }
            for card in cards
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_trivial_single_requirement_section(rows: list[dict]) -> bool:
    if len(rows) != 1:
        return False
    row = rows[0] or {}
    item_name = _normalize_requirement_text(str(row.get("항목명") or row.get("item_name") or ""))
    requirement = _normalize_requirement_text(str(row.get("요구사항") or row.get("requirement") or ""))
    detail_requirement = _normalize_requirement_text(str(row.get("상세요건") or row.get("detail_requirement") or ""))
    if not item_name or not requirement or not detail_requirement:
        return False
    if item_name == requirement == detail_requirement:
        return True
    part_value = _normalize_requirement_text(str(row.get("Part") or row.get("part") or row.get("group") or ""))
    section_value = _normalize_requirement_text(str(row.get("Section") or row.get("section") or ""))
    category_value = _normalize_requirement_text(str(row.get("Category") or row.get("카테고리") or row.get("category") or row.get("requirement") or ""))
    meta_label_hits = sum(1 for token in ("Part", "Section", "Category", "카테고리") if token.lower() in detail_requirement.lower())
    if meta_label_hits >= 2:
        return True
    if part_value and section_value and category_value:
        if part_value in detail_requirement and section_value in detail_requirement and category_value in detail_requirement:
            return True
    return False


def _normalize_section_requirement_tables(section_tables: dict[str, list[dict]]) -> dict[str, list[dict]]:
    normalized_tables: dict[str, list[dict]] = {}
    for section_name, rows in section_tables.items():
        normalized_rows: list[dict] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            part_value = str(row.get("Part") or row.get("part") or row.get("group") or "").strip()
            section_value = str(row.get("Section") or row.get("section") or "").strip()
            cleaned_row = {
                "블럭명": row.get("블럭명", row.get("block_name", row.get("card_no", row.get("_card_no", "")))),
                "요구사항 ID": row.get("요구사항 ID", ""),
                "항목명": row.get("항목명", ""),
                "요구사항": row.get("요구사항", ""),
                "상세요건": row.get("상세요건", ""),
                "추가정보": row.get("추가정보", ""),
                "페이지": row.get("페이지", ""),
                "Part": part_value,
                "Section": section_value,
                "Category": row.get("Category", row.get("카테고리", "")),
                "생성 출처": row.get("생성 출처", ""),
                "생성 방식": row.get("생성 방식", ""),
            }
            for key, value in row.items():
                if key in cleaned_row or key in {"적용룰", "applied_rule"}:
                    continue
                cleaned_row[key] = value
            normalized_rows.append(cleaned_row)
        normalized_tables[section_name] = normalized_rows
    return normalized_tables


def _cards_html_excerpt_rows(cards: list[RfpCard]) -> list[dict]:
    rows: list[dict] = []
    for card in cards:
        html_excerpt = str(card.html_excerpt or "").strip()
        if not html_excerpt:
            rows.append(
                {
                    "card_id": card.card_id,
                    "card_no": card.card_no,
                    "requirement": card.requirement,
                    "subject": card.subject,
                    "sub_subject": card.sub_subject,
                    "part": getattr(card, "part", None) or "",
                    "section": card.section,
                    "row_no": 1,
                    "tag": "",
                    "text": "",
                    "html": "",
                }
            )
            continue

        soup = BeautifulSoup(html_excerpt, "html.parser")
        blocks = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "tr", "td", "th", "ul", "ol"])
        if not blocks:
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
            rows.append(
                {
                    "card_id": card.card_id,
                    "requirement": card.requirement,
                    "part": getattr(card, "part", None) or "",
                    "section": card.section,
                    "row_no": 1,
                    "tag": "text",
                    "text": text,
                    "html": html_excerpt,
                }
            )
            continue

        for row_no, block in enumerate(blocks, start=1):
            text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()
            if not text:
                continue
            rows.append(
                {
                    "card_id": card.card_id,
                    "card_no": card.card_no,
                    "requirement": card.requirement,
                    "subject": card.subject,
                    "sub_subject": card.sub_subject,
                    "part": getattr(card, "part", None) or "",
                    "section": card.section,
                    "row_no": row_no,
                    "tag": block.name,
                    "text": text,
                    "html": str(block),
                }
            )
    return rows


def _normalize_title_for_match(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip().lower()
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇\(\)\[\]]+\s*", "", compact)
    compact = re.sub(r"^[ivxlcdm]+\b[\.\-\)]?\s+", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"^\d+(?:\.\d+)*[\.\-\)]?\s+", "", compact)
    compact = re.sub(r"^제\s*\d+\s*(장|절|항)\s*", "", compact)
    return compact


def _normalize_title_for_relaxed_match(value: str) -> str:
    compact = _normalize_title_for_match(value)
    if not compact:
        return ""
    compact = re.sub(r"[\s\-\–\—_·•,，.:;·'\"`/\\()\[\]{}]+", "", compact)
    compact = re.sub(r"[※#]+", "", compact)
    return compact


def _split_heading_prefix(value: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", " ", (value or "")).strip()
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇\(\)\[\]]+\s*", "", compact)
    if not compact:
        return "", ""
    match = re.match(
        r"^(?P<prefix>(?:\d+(?:\.\d+)*|[ivxlcdm]+(?:\.[ivxlcdm]+)*|제\s*\d+\s*(?:장|절|항))[\.\-\)]?)\s*(?P<rest>.*)$",
        compact,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", compact
    prefix = re.sub(r"\s+", " ", match.group("prefix")).strip()
    rest = re.sub(r"\s+", " ", match.group("rest")).strip()
    return prefix, rest


def _title_match(expected: str, actual: str) -> bool:
    expected_compact = re.sub(r"\s+", " ", (expected or "")).strip()
    actual_compact = re.sub(r"\s+", " ", (actual or "")).strip()
    if not expected_compact or not actual_compact:
        return False
    if expected_compact.lower() == actual_compact.lower():
        return True

    expected_prefix, expected_rest = _split_heading_prefix(expected_compact)
    actual_prefix, actual_rest = _split_heading_prefix(actual_compact)
    expected_prefix_key = _title_key(expected_prefix) if expected_prefix else ""
    actual_prefix_key = _title_key(actual_prefix) if actual_prefix else ""
    expected_rest_key = _title_key(expected_rest) if expected_rest else ""
    actual_rest_key = _title_key(actual_rest) if actual_rest else ""

    if expected_rest_key:
        if expected_prefix_key and actual_prefix_key and expected_prefix_key != actual_prefix_key:
            return False
        if actual_rest_key == expected_rest_key or actual_rest_key.startswith(expected_rest_key):
            return True
        # Roman chapter titles are often rewritten slightly between TOC and body
        # (for example "Ⅱ 업무 현황" vs "II. 대상업무 현황"). If the prefixes
        # match and one rest phrase clearly contains the other, treat it as the
        # same section so the boundary does not leak into the previous card.
        return bool(expected_rest_key and actual_rest_key and (expected_rest_key in actual_rest_key or actual_rest_key in expected_rest_key))

    if expected_prefix_key:
        return actual_prefix_key == expected_prefix_key

    expected_key = _title_key(expected_compact)
    actual_key = _title_key(actual_compact)
    if not expected_key or not actual_key:
        return False
    if expected_key == actual_key:
        return True
    expected_relaxed = _normalize_title_for_relaxed_match(expected_compact)
    actual_relaxed = _normalize_title_for_relaxed_match(actual_compact)
    if expected_relaxed and actual_relaxed:
        if expected_relaxed == actual_relaxed:
            return True
        if expected_relaxed.startswith(actual_relaxed) or actual_relaxed.startswith(expected_relaxed):
            return True
    return actual_key.startswith(expected_key)


def _title_key(value: str) -> str:
    compact = _normalize_title_for_match(value)
    return re.sub(r"[\W_]+", "", compact, flags=re.UNICODE)


def _titles_match_with_suffix(expected: str, actual: str) -> bool:
    expected_norm = _normalize_title_for_match(expected)
    actual_norm = _normalize_title_for_match(actual)
    expected_key = _title_key(expected)
    actual_key = _title_key(actual)
    if not expected_norm or not actual_norm or not expected_key or not actual_key:
        return False
    if expected_key == actual_key:
        return True
    if not actual_key.startswith(expected_key):
        return False
    suffix = actual_norm[len(expected_norm) :].strip()
    if not suffix:
        return True
    return bool(re.match(r"^[\s\-/:\.\)\(·•\u00b7,，;；\]]", suffix))


def _is_heading_like_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return False
    if re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+)\s*", compact, flags=re.IGNORECASE):
        return True
    if re.match(r"^[가나다라마바사아자차카타파하]\.\s*", compact):
        return True
    return False


def _is_toc_like_body_line(text: str) -> bool:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return False
    if re.search(r"[.\-_·•\s]{2,}\d{1,4}\s*$", compact):
        return True
    if re.match(r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+|[가나다라마바사아자차카타파하]\.)\s+", compact, flags=re.IGNORECASE):
        return bool(re.search(r"\d{1,4}\s*$", compact))
    return False


def _detect_body_start_index(body_blocks: list[dict], toc_items: list[TocItem]) -> int:
    if not body_blocks:
        return 0

    toc_keys = {_title_key(item.title) for item in toc_items if item.title}
    if not toc_keys:
        return 0

    for idx, block in enumerate(body_blocks):
        text = block["text"]
        tag = block["tag"]
        key = _title_key(text)
        is_heading = tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or _is_heading_like_text(text)
        if not is_heading and tag == "li" and key in toc_keys and len(text) <= 40 and not _is_toc_like_body_line(text):
            is_heading = True
        if not is_heading:
            continue
        if key not in toc_keys:
            continue

        lookahead = body_blocks[idx + 1 : idx + 5]
        toc_like_count = sum(1 for candidate in lookahead if _is_toc_like_body_line(candidate["text"]))
        if toc_like_count >= 2:
            continue
        return idx

    return 0


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

    normalized = re.sub(
        r"\[(?P<inner>\s*\d+\.\s*\n\s*\d+\.[^\]]*?)\]",
        _repl_bracket,
        normalized,
        flags=re.DOTALL,
    )
    return normalized


def _parse_style_indentation(style: str | None) -> float | None:
    if not style:
        return None
    text = str(style).strip()
    if not text:
        return None
    matches = re.findall(r"(margin-left|padding-left|text-indent)\s*:\s*([-+]?\d*\.?\d+)\s*(px|pt|em|rem)?", text, flags=re.IGNORECASE)
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


def _body_text_blocks(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    blocks: list[dict] = []
    candidates = body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "div", "section", "article", "figcaption"], recursive=True)
    for tag in candidates:
        if tag.name == "table":
            if tag.find_parent("table") is not None:
                continue
        else:
            if tag.find_parent(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "figcaption"]) is not None:
                continue
            if tag.find_parent("table") is not None:
                continue
        if tag.name in {"div", "section", "article"}:
            if tag.find(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"], recursive=True):
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
            blocks.append({"html": html_chunk, "text": text, "tag": tag.name, "indent_px": indent_px, "page_idx": page_idx})
            continue

        if tag.name in {"h1", "h2", "h3", "h4", "h5", "h6", "figcaption"}:
            blocks.append({"html": html_chunk, "text": text, "tag": tag.name, "indent_px": indent_px, "page_idx": page_idx})
            continue

        line_source = _tag_without_nested_tables(tag).find(tag.name) or tag
        lines = [line for line in _extract_lines_from_tag(line_source) if line.strip()]
        if len(lines) > 1:
            for line_idx, line in enumerate(lines, start=1):
                for part in _split_embedded_heading_suffixes(line):
                    line_html = f"<{tag.name}>{escape(part)}</{tag.name}>"
                    blocks.append({"html": line_html, "text": part, "tag": tag.name, "indent_px": indent_px, "page_idx": page_idx})
            continue

        split_parts = _split_embedded_heading_suffixes(text)
        if len(split_parts) > 1:
            for part in split_parts:
                part_html = f"<{tag.name}>{escape(part)}</{tag.name}>"
                blocks.append({"html": part_html, "text": part, "tag": tag.name, "indent_px": indent_px, "page_idx": page_idx})
            continue

        blocks.append({"html": html_chunk, "text": text, "tag": tag.name, "indent_px": indent_px, "page_idx": page_idx})
    return blocks


def _extract_hierarchical_body_rows_from_html(
    html: str,
    title: str = "",
    default_item_name: str = "",
    section_context: dict | None = None,
) -> list[dict]:
    blocks = [block for block in _body_text_blocks(html) if block.get("tag") != "table"]
    if not blocks:
        return []

    normalized_title = _normalize_requirement_text(title)
    default_level1 = _normalize_requirement_text(default_item_name) or normalized_title
    entries: list[str] = []

    for block in blocks:
        block_html = str(block.get("html") or "")
        if not block_html:
            continue
        block_soup = BeautifulSoup(block_html, "html.parser")
        block_tag = block_soup.find(str(block.get("tag") or "")) or block_soup
        for raw_line in _extract_lines_from_tag(block_tag):
            text = _normalize_requirement_text(raw_line)
            if not text or _is_only_page_info(text) or _is_section_reference_note_line(text):
                continue
            entries.append(_collapse_repeated_token_tail(text))

    if not entries:
        return []

    while normalized_title and entries and _normalize_requirement_text(entries[0]) == normalized_title:
        entries.pop(0)
    if not entries:
        return []
    return _extract_atomic_body_rows(
        entries,
        title=normalized_title,
        default_item_name=default_level1,
        build_method="룰 기반(단일 행 본문)",
    )


def _block_title_candidates(block: dict) -> list[str]:
    candidates: list[str] = []
    tag = str(block.get("tag") or "").lower()
    text = str(block.get("text") or "").strip()
    if text and (tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or not _is_citation_like_text(text)):
        candidates.append(text)

    html = str(block.get("html") or "").strip()
    if html:
        soup = BeautifulSoup(html, "html.parser")
        raw_lines = soup.get_text("\n", strip=False).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for line in raw_lines:
            normalized = re.sub(r"\s+", " ", line).strip()
            if normalized and normalized not in candidates and (tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or not _is_citation_like_text(normalized)):
                candidates.append(normalized)
        for cell in soup.find_all(["th", "td", "p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
            normalized = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            if normalized and normalized not in candidates and (cell.name in {"h1", "h2", "h3", "h4", "h5", "h6"} or not _is_citation_like_text(normalized)):
                candidates.append(normalized)
    return candidates


def _is_citation_like_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return False
    return bool(
        re.match(r"^[『「“‘].+[』」”’]$", compact)
        or re.match(r"^['\"].+['\"]$", compact)
        or re.match(r"^『[^』]{1,120}』\s*(?:을|를|은|는|이|가|에|로|로서|에서|에 대한|을 위한|을 통해)", compact)
        or re.match(r"^「[^」]{1,120}」\s*(?:을|를|은|는|이|가|에|로|로서|에서|에 대한|을 위한|을 통해)", compact)
        or re.match(r"^“[^”]{1,120}”\s*(?:을|를|은|는|이|가|에|로|로서|에서|에 대한|을 위한|을 통해)", compact)
        or re.match(r"^‘[^’]{1,120}’\s*(?:을|를|은|는|이|가|에|로|로서|에서|에 대한|을 위한|을 통해)", compact)
    )


def _extract_body_heading_candidates(html: str) -> list[dict]:
    blocks = _body_text_blocks(html)
    heading_blocks: list[dict] = []
    for idx, block in enumerate(blocks):
        text = block["text"]
        tag = block["tag"]
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or _is_heading_like_text(text):
            heading_blocks.append({"index": idx, "text": text, "html": block["html"], "tag": tag})
    return heading_blocks


def _find_title_start_index(body_blocks: list[dict], title: str, start_from: int = 0) -> int | None:
    if not re.sub(r"\s+", "", (title or "")).strip():
        return None
    if not body_blocks:
        return None
    is_high_level_title = _is_high_level_section_title(title)
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        if is_high_level_title and str(block.get("tag") or "").lower() == "table":
            continue
        candidates = _block_title_candidates(block)
        if is_high_level_title:
            block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
            candidates = [block_text] if block_text else []
        for candidate in candidates:
            if _title_match(title, candidate):
                return idx
    if is_high_level_title:
        fallback_idx = max(start_from, 0)
        return fallback_idx if fallback_idx < len(body_blocks) else None
    return None


def _title_candidate_score(block: dict, title: str, candidate: str) -> tuple[int, int]:
    tag = str(block.get("tag") or "").lower()
    text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    candidate_text = re.sub(r"\s+", " ", candidate).strip()
    expected_key = _title_key(title)
    candidate_key = _title_key(candidate_text)
    if not expected_key or not candidate_key:
        return -1, -1
    if not _title_match(title, candidate_text):
        return -1, -1
    heading_bonus = 100 if (tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or _is_heading_like_text(text)) else 0
    exact_bonus = 50 if _normalize_title_for_match(title) == _normalize_title_for_match(candidate_text) else 0
    prefix_bonus = 20 if candidate_key.startswith(expected_key) else 0
    length_bonus = min(len(candidate_key), len(expected_key))
    return heading_bonus + exact_bonus + prefix_bonus + length_bonus, len(candidate_key)


def _is_high_level_section_title(title: str) -> bool:
    compact = re.sub(r"\s+", " ", (title or "")).strip()
    if not compact:
        return False
    return bool(
        re.match(r"^(?:[IVXLCDM]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)[\.\)]?\s*.+", compact, flags=re.IGNORECASE)
        or re.match(r"^제?\s*\d+\s*(?:장|절|항)[\.\)]?\s*.+", compact, flags=re.IGNORECASE)
        or re.match(r"^\d+\.\s*.+", compact)
    )


def _find_title_start_index_with_page(
    body_blocks: list[dict],
    title: str,
    start_from: int = 0,
    page_idx: int | None = None,
) -> int | None:
    if not body_blocks:
        return None
    best_idx: int | None = None
    best_score: tuple[int, int] = (-1, -1)
    is_high_level_title = _is_high_level_section_title(title)
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        if is_high_level_title and str(block.get("tag") or "").lower() == "table":
            continue
        block_page_idx = block.get("page_idx")
        if page_idx is not None and block_page_idx is not None and block_page_idx != page_idx:
            continue
        candidates = _block_title_candidates(block)
        if is_high_level_title:
            block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
            candidates = [block_text] if block_text else []
        for candidate in candidates:
            if is_high_level_title and str(block.get("tag") or "").lower() not in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "div", "section", "article", "figcaption"}:
                continue
            score = _title_candidate_score(block, title, candidate)
            if score > best_score:
                best_idx = idx
                best_score = score
    if best_idx is not None:
        return best_idx
    if is_high_level_title:
        fallback_idx = max(start_from, 0)
        return fallback_idx if fallback_idx < len(body_blocks) else None
    return _find_title_start_index(body_blocks, title, start_from)


def _anchor_match(anchor: str, candidate: str) -> bool:
    anchor_text = _normalize_requirement_text(anchor)
    candidate_text = _normalize_requirement_text(candidate)
    if not anchor_text or not candidate_text:
        return False
    anchor_slug = _anchor_from_text(anchor_text)
    candidate_slug = _anchor_from_text(candidate_text)
    if anchor_slug and candidate_slug:
        return anchor_slug.lower() == candidate_slug.lower()
    return anchor_text.lower() == candidate_text.lower()


def _anchor_candidate_score(block: dict, anchor: str, candidate: str) -> tuple[int, int]:
    anchor_text = _normalize_requirement_text(anchor)
    candidate_text = _normalize_requirement_text(candidate)
    if not anchor_text or not candidate_text:
        return -1, -1
    if not _anchor_match(anchor_text, candidate_text):
        return -1, -1

    anchor_slug = _anchor_from_text(anchor_text).lower()
    candidate_slug = _anchor_from_text(candidate_text).lower()
    anchor_relaxed = _normalize_title_for_relaxed_match(anchor_text)
    candidate_relaxed = _normalize_title_for_relaxed_match(candidate_text)
    tag = str(block.get("tag") or "").lower()
    text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    heading_bonus = 20 if (tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or _is_heading_like_text(text)) else 0

    if anchor_slug and candidate_slug and anchor_slug == candidate_slug:
        return 300 + heading_bonus, len(candidate_slug)
    if anchor_text.lower() == candidate_text.lower():
        return 250 + heading_bonus, len(candidate_text)
    if anchor_relaxed and candidate_relaxed and anchor_relaxed == candidate_relaxed:
        return 220 + heading_bonus, len(candidate_relaxed)
    return 120 + heading_bonus, len(candidate_text)


def _find_anchor_start_index_with_page(
    body_blocks: list[dict],
    anchor: str,
    start_from: int = 0,
    page_idx: int | None = None,
) -> int | None:
    compact_anchor = re.sub(r"\s+", " ", (anchor or "")).strip()
    if not compact_anchor:
        return None
    if not body_blocks:
        return None
    best_idx: int | None = None
    best_score: tuple[int, int] = (-1, -1)
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        block_page_idx = block.get("page_idx")
        if page_idx is not None and block_page_idx is not None and block_page_idx != page_idx:
            continue
        candidates = list(_block_title_candidates(block))
        raw_text = _normalize_requirement_text(str(block.get("text") or ""))
        if raw_text:
            candidates.append(raw_text)
        html = str(block.get("html") or "").strip()
        if html:
            soup = BeautifulSoup(html, "html.parser")
            flattened_html_text = _normalize_requirement_text(soup.get_text(" ", strip=True))
            if flattened_html_text:
                candidates.append(flattened_html_text)
            for tag in soup.find_all(True):
                raw_id = tag.get("id")
                if raw_id:
                    candidates.append(str(raw_id).strip())
                for attr_name in ("data-anchor", "data-title", "name"):
                    raw_attr = tag.get(attr_name)
                    if raw_attr:
                        candidates.append(str(raw_attr).strip())
        for candidate in candidates:
            if not candidate:
                continue
            if _anchor_match(compact_anchor, candidate):
                score = _anchor_candidate_score(block, compact_anchor, candidate)
                if score > best_score:
                    best_idx = idx
                    best_score = score
    if best_idx is not None:
        return best_idx
    return None


def _debug_title_match_candidates(
    body_blocks: list[dict],
    title: str,
    start_from: int = 0,
    context_size: int = 2,
    *,
    match_query: str | None = None,
) -> dict:
    expected = _normalize_title_for_match(title)
    query = (match_query or title or "").strip()
    expected_key = _title_key(query)
    expected_prefix, expected_rest = _split_heading_prefix(query)
    is_high_level_title = _is_high_level_section_title(query or title)
    use_query_only = bool(match_query and _normalize_title_for_match(match_query) != _normalize_title_for_match(title))
    debug: dict = {
        "title": title,
        "match_query": query,
        "match_mode": "anchor" if use_query_only else "title",
        "start_from": start_from,
        "expected": expected,
        "expected_key": expected_key,
        "expected_prefix": expected_prefix,
        "expected_rest": expected_rest,
        "matched": False,
        "matched_index": None,
        "matched_candidate": "",
        "matched_block_text": "",
        "matched_text": "",
        "matched_tag": "",
        "reason": "",
        "nearby_blocks": [],
        "all_candidate_hits": [],
    }
    if not expected_key:
        debug["reason"] = "empty_expected_key"
        return debug

    best_match_index: int | None = None
    best_match_reason = ""
    best_match_candidate = ""
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        if is_high_level_title and str(block.get("tag") or "").lower() == "table":
            continue
        candidates = _block_title_candidates(block)
        raw_text = _normalize_requirement_text(str(block.get("text") or ""))
        if raw_text:
            candidates.append(raw_text)
        if is_high_level_title and not use_query_only:
            block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
            candidates = [block_text] if block_text else []
        elif not use_query_only:
            html = str(block.get("html") or "").strip()
            if html:
                candidates.append(_normalize_requirement_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)))
        elif use_query_only:
            html = str(block.get("html") or "").strip()
            if html:
                soup = BeautifulSoup(html, "html.parser")
                flattened_html_text = _normalize_requirement_text(soup.get_text(" ", strip=True))
                if flattened_html_text:
                    candidates.append(flattened_html_text)
                for tag in soup.find_all(True):
                    raw_id = tag.get("id")
                    if raw_id:
                        candidates.append(str(raw_id).strip())
                    for attr_name in ("data-anchor", "data-title", "name"):
                        raw_attr = tag.get(attr_name)
                        if raw_attr:
                            candidates.append(str(raw_attr).strip())
        for candidate in candidates:
            if use_query_only:
                if not _anchor_match(query, candidate):
                    continue
            elif not _title_match(title, candidate):
                continue
            if use_query_only or _anchor_match(query, candidate):
                best_match_index = idx
                best_match_candidate = candidate
                actual_prefix, actual_rest = _split_heading_prefix(candidate)
                if expected_rest and expected_prefix:
                    best_match_reason = "prefix+rest" if actual_rest else "prefix"
                elif expected_prefix:
                    best_match_reason = "prefix"
                else:
                    best_match_reason = "exact"
                break
        if best_match_index is not None:
            break

    if best_match_index is not None:
        block = body_blocks[best_match_index]
        debug["matched"] = True
        debug["matched_index"] = best_match_index
        debug["matched_candidate"] = best_match_candidate
        debug["matched_block_text"] = block.get("text", "")
        debug["matched_text"] = block.get("text", "")
        debug["matched_tag"] = block.get("tag", "")
        debug["reason"] = best_match_reason
    elif is_high_level_title and not use_query_only and 0 <= start_from < len(body_blocks):
        block = body_blocks[start_from]
        debug["matched"] = True
        debug["matched_index"] = start_from
        debug["matched_candidate"] = title
        debug["matched_block_text"] = block.get("text", "")
        debug["matched_text"] = block.get("text", "")
        debug["matched_tag"] = block.get("tag", "")
        debug["reason"] = "high_level_fallback"
    else:
        debug["reason"] = "not_found_after_cursor"

    for idx, block in enumerate(body_blocks):
        if is_high_level_title and str(block.get("tag") or "").lower() == "table":
            continue
        candidates = _block_title_candidates(block)
        if is_high_level_title and not use_query_only:
            block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
            candidates = [block_text] if block_text else []
        elif not use_query_only:
            html = str(block.get("html") or "").strip()
            if html:
                candidates.append(_normalize_requirement_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)))
        elif use_query_only:
            html = str(block.get("html") or "").strip()
            if html:
                soup = BeautifulSoup(html, "html.parser")
                flattened_html_text = _normalize_requirement_text(soup.get_text(" ", strip=True))
                if flattened_html_text:
                    candidates.append(flattened_html_text)
                for tag in soup.find_all(True):
                    raw_id = tag.get("id")
                    if raw_id:
                        candidates.append(str(raw_id).strip())
                    for attr_name in ("data-anchor", "data-title", "name"):
                        raw_attr = tag.get(attr_name)
                        if raw_attr:
                            candidates.append(str(raw_attr).strip())
        for candidate in candidates:
            if use_query_only:
                if not _anchor_match(query, candidate):
                    continue
            elif not _title_match(title, candidate):
                continue
            debug["all_candidate_hits"].append(
                {
                    "index": idx,
                    "tag": block.get("tag", ""),
                    "text": candidate,
                    "key": _title_key(candidate),
                }
            )
            break

    if best_match_index is None:
        lower = max(start_from - context_size, 0)
        upper = min(len(body_blocks), start_from + context_size + 1)
        debug["nearby_blocks"] = [
            {
                "index": idx,
                "tag": body_blocks[idx].get("tag", ""),
                "text": body_blocks[idx].get("text", ""),
            }
            for idx in range(lower, upper)
        ]
    else:
        lower = max(best_match_index - context_size, 0)
        upper = min(len(body_blocks), best_match_index + context_size + 1)
        debug["nearby_blocks"] = [
            {
                "index": idx,
                "tag": body_blocks[idx].get("tag", ""),
                "text": body_blocks[idx].get("text", ""),
            }
            for idx in range(lower, upper)
        ]

    return debug


def _split_leading_title_block(block: dict, title: str) -> list[dict]:
    block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    title_text = re.sub(r"\s+", " ", (title or "")).strip()
    if not block_text or not title_text:
        return [block]
    if block_text.lower() == title_text.lower():
        return [block]
    if not block_text.lower().startswith(title_text.lower()):
        return [block]

    remainder = block_text[len(title_text):].strip()
    if not remainder:
        return [block]

    tag = str(block.get("tag") or "p").strip().lower() or "p"
    title_html = f"<{tag}><p>{escape(title_text)}</p></{tag}>" if tag == "li" else f"<{tag}>{escape(title_text)}</{tag}>"
    remainder_html = f"<p>{escape(remainder)}</p>"
    return [
        {"html": title_html, "text": title_text, "tag": tag},
        {"html": remainder_html, "text": remainder, "tag": "p"},
    ]


def _build_sections_from_final_toc(html: str, toc_items: list[TocItem]) -> list[Section]:
    soup = BeautifulSoup(html, "html.parser")
    # 일부 문서(JB 계열처럼)에서는 본문 시작점 분리가 너무 뒤로 밀려
    # 실제 섹션 제목이 body_region 밖(pre 쪽)에 남는 경우가 있다.
    # 이런 경우까지 놓치지 않도록 전체 body 기준 블록을 우선 사용한다.
    body_blocks = _body_text_blocks(str(soup.body or soup))
    body_start_idx = _detect_body_start_index(body_blocks, toc_items)
    if body_start_idx > 0:
        body_blocks = body_blocks[body_start_idx:]
    matched_positions: list[tuple[int, int, TocItem]] = []
    match_debug: list[dict] = []
    candidate_cursor = 0
    for toc_idx, item in enumerate(toc_items):
        match_key = _normalize_requirement_text(str(item.anchor or "")).strip() or item.title
        debug_entry = _debug_title_match_candidates(body_blocks, item.title, candidate_cursor, match_query=match_key)
        debug_entry["toc_index"] = toc_idx
        debug_entry["level"] = item.level
        debug_entry["page_idx"] = item.page_idx
        debug_entry["match_key"] = match_key
        debug_entry["match_anchor"] = item.anchor
        start_idx = None
        if item.anchor and _normalize_requirement_text(str(item.anchor)):
            start_idx = _find_anchor_start_index_with_page(body_blocks, item.anchor, candidate_cursor, item.page_idx)
            if start_idx is not None:
                debug_entry["matched_by"] = "anchor"
            elif candidate_cursor > 0:
                start_idx = _find_anchor_start_index_with_page(body_blocks, item.anchor, 0, item.page_idx)
                if start_idx is not None:
                    debug_entry["matched_by"] = "anchor_global"
        if start_idx is None and not item.anchor:
            start_idx = _find_title_start_index_with_page(body_blocks, item.title, candidate_cursor, item.page_idx)
            if start_idx is not None and not debug_entry.get("matched_by"):
                debug_entry["matched_by"] = "title"
        if start_idx is None and item.anchor:
            debug_entry["reason"] = "anchor_not_found"
            debug_entry["matched_by"] = "anchor"
        elif start_idx is None:
            debug_entry["reason"] = "title_not_found"
        debug_entry["resolved_index"] = start_idx
        if start_idx is not None:
            matched_block = body_blocks[start_idx]
            debug_entry["matched_page_idx"] = matched_block.get("page_idx")
        match_debug.append(debug_entry)
        if start_idx is None:
            continue
        matched_positions.append((start_idx, toc_idx, item))
        candidate_cursor = start_idx + 1

    matched_positions.sort(key=lambda entry: (entry[0], entry[1]))
    span_end_by_start: dict[int, int] = {}
    for idx, (start_idx, _, _) in enumerate(matched_positions):
        next_start = matched_positions[idx + 1][0] if idx + 1 < len(matched_positions) else len(body_blocks)
        span_end_by_start[start_idx] = next_start

    matched_titles_by_toc_index = {toc_idx: (start_idx, item) for start_idx, toc_idx, item in matched_positions}
    ordered: list[Section] = []
    for toc_idx, item in enumerate(toc_items):
        matched = matched_titles_by_toc_index.get(toc_idx)
        if matched:
            start_idx, _ = matched
            end_idx = span_end_by_start.get(start_idx, len(body_blocks))
            section_blocks = list(body_blocks[start_idx:end_idx])
            if section_blocks and (not item.anchor or _normalize_title_for_match(item.anchor) == _normalize_title_for_match(item.title)):
                section_blocks = _split_leading_title_block(section_blocks[0], item.title) + section_blocks[1:]
            html_source = "\n".join(block["html"] for block in section_blocks).strip()
            text_source = " ".join(block["text"] for block in section_blocks).strip()
            ordered.append(
                Section(
                    title=item.title,
                    anchor=item.anchor or _slugify(item.title),
                    level=item.level,
                    page_idx=item.page_idx,
                    html=html_source,
                    text=text_source,
                )
            )
            continue
        ordered.append(
            Section(
                title=item.title,
                anchor=item.anchor,
                level=item.level,
                page_idx=item.page_idx,
                html="",
                text="",
            )
        )
    st.session_state["step3_toc_match_debug"] = match_debug
    return ordered


def _build_cards_from_sections(sections: list[Section]) -> list[RfpCard]:
    cards = build_rfp_cards(sections)
    return _repair_misattached_leading_tables(cards)


def _card_contains_table(card: RfpCard) -> bool:
    return "<table" in str(getattr(card, "html_excerpt", "") or "").lower()


def _card_plain_lines(card: RfpCard) -> list[str]:
    return [
        line.strip()
        for line in _plain_text_from_html_excerpt(str(getattr(card, "html_excerpt", "") or "")).splitlines()
        if line.strip()
    ]


def _looks_like_table_intro_card(card: RfpCard) -> bool:
    if _card_contains_table(card):
        return False
    lines = _card_plain_lines(card)
    if len(lines) < 2:
        return False
    body_lines = lines[1:]
    joined = " ".join(body_lines)
    intro_markers = (
        "다음과 같습니다",
        "다음과 같",
        "아래와 같습니다",
        "아래와 같",
        "다음 서류",
        "다음 표",
        "다음과 같은",
    )
    return any(marker in joined for marker in intro_markers)


def _split_card_heading_table_tail(card: RfpCard) -> tuple[list[dict], list[dict], list[dict]]:
    blocks = _body_text_blocks(str(getattr(card, "html_excerpt", "") or ""))
    if not blocks:
        return [], [], []

    leading_heading: list[dict] = []
    leading_tables: list[dict] = []
    trailing_blocks: list[dict] = []
    idx = 0
    while idx < len(blocks) and blocks[idx].get("tag") != "table":
        leading_heading.append(blocks[idx])
        idx += 1
    while idx < len(blocks) and blocks[idx].get("tag") == "table":
        leading_tables.append(blocks[idx])
        idx += 1
    trailing_blocks = blocks[idx:]
    return leading_heading, leading_tables, trailing_blocks


def _repair_misattached_leading_tables(cards: list[RfpCard]) -> list[RfpCard]:
    if not cards:
        return cards

    repaired = list(cards)
    for idx, card in enumerate(repaired):
        if not _looks_like_table_intro_card(card):
            continue

        for look_ahead in range(idx + 1, min(idx + 4, len(repaired))):
            candidate = repaired[look_ahead]
            if str(getattr(candidate, "section", "") or "") != str(getattr(card, "section", "") or ""):
                break
            if not _card_contains_table(candidate):
                continue

            leading_heading, leading_tables, trailing_blocks = _split_card_heading_table_tail(candidate)
            if not leading_tables:
                continue
            if not leading_heading:
                continue

            # Only repair when the later card starts with its own heading and
            # the table is placed before the card's actual body content.
            if not trailing_blocks:
                continue

            repaired[idx] = _clone_card(
                card,
                subject=getattr(card, "subject", None) or card.requirement,
                card_no=getattr(card, "card_no", None) or str(card.card_id),
            )
            repaired[idx].html_excerpt = (
                str(getattr(card, "html_excerpt", "") or "").strip()
                + "\n"
                + "\n".join(block["html"] for block in leading_tables).strip()
            ).strip()

            repaired[look_ahead] = _clone_card(
                candidate,
                subject=getattr(candidate, "subject", None) or candidate.requirement,
                card_no=getattr(candidate, "card_no", None) or str(candidate.card_id),
            )
            repaired[look_ahead].html_excerpt = "\n".join(
                block["html"] for block in [*leading_heading, *trailing_blocks]
            ).strip()
            break
    return repaired


def _load_openai_api_key() -> str:
    return load_openai_api_key()


def _clone_card(
    card: RfpCard,
    *,
    subject: str | None = None,
    card_no: str | None = None,
    card_id: int | None = None,
    category: str | None = None,
) -> RfpCard:
    return RfpCard(
        card_id=card_id if card_id is not None else card.card_id,
        card_no=card_no if card_no is not None else getattr(card, "card_no", None),
        requirement=card.requirement,
        subject=subject if subject is not None else (getattr(card, "subject", None) or card.requirement),
        sub_subject=getattr(card, "sub_subject", None),
        body_fragment_level=getattr(card, "body_fragment_level", None),
        part=getattr(card, "part", None) or "",
        section=card.section,
        category=category if category is not None else getattr(card, "category", None),
        html_excerpt=card.html_excerpt,
        page_idx=card.page_idx,
        anchor=card.anchor,
    )


def _split_card_payload(card: RfpCard) -> dict:
    return {
        "card_id": card.card_id,
        "card_no": getattr(card, "card_no", None) or str(card.card_id),
        "requirement": card.requirement,
        "subject": getattr(card, "subject", None) or card.requirement,
        "category": getattr(card, "category", None) or "",
        "sub_subject": getattr(card, "sub_subject", None) or "",
        "body_fragment_level": getattr(card, "body_fragment_level", None),
        "part": getattr(card, "part", None) or "",
        "section": card.section,
        "page_idx": card.page_idx,
        "anchor": card.anchor,
        "html_excerpt": card.html_excerpt,
    }


def _infer_table_hierarchy_hint(table_text: str) -> str:
    text = re.sub(r"\s+", " ", table_text or "").strip()
    if not text:
        return "none"
    keys = ["항목명", "항목", "요구사항", "상세요건", "세부요건", "설명", "내용", "기준", "주요내용"]
    hit = [key for key in keys if key in text]
    if {"항목명", "요구사항", "상세요건"}.issubset(set(hit)):
        return "3-level: item_name > requirement > detail"
    if {"요구사항", "상세요건"}.issubset(set(hit)):
        return "2-level: requirement > detail"
    if {"항목", "설명"}.issubset(set(hit)) or {"항목", "내용"}.issubset(set(hit)):
        return "2-level: item > description"
    return f"columns-hints: {', '.join(hit) if hit else 'none'}"


def _detect_bullet_level(text: str) -> int | None:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return None
    if re.match(r"^[IVXLCDM]+\.\s+", compact, flags=re.IGNORECASE):
        return 1
    if re.match(r"^\d+\.\d+\.\d+", compact):
        return 4
    if re.match(r"^\d+\.\d+", compact):
        return 3
    if re.match(r"^\d+\)\s+", compact):
        return 3
    if re.match(r"^[가나다라마바사아자차카타파하]\.\s+", compact):
        return 2
    if re.match(r"^[-•·▪■◆▶]\s+", compact):
        return 4
    return None


def _split_card_context(card: RfpCard) -> dict:
    html_excerpt = str(card.html_excerpt or "").strip()
    return {
        "top_text": re.sub(r"\s+", " ", html_excerpt).strip()[:5000],
        "body_fragment_level": getattr(card, "body_fragment_level", None),
    }


def _partition_card_into_table_body_segments(card: RfpCard) -> list[RfpCard]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if not html_excerpt:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    blocks = _body_text_blocks(html_excerpt)
    if not blocks:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    segments: list[tuple[str, list[dict]]] = []

    def _carry_table_intro_blocks(prev_blocks: list[dict]) -> list[dict]:
        carried: list[dict] = []
        if not prev_blocks:
            return carried
        def _looks_complete(text: str) -> bool:
            compact = _normalize_requirement_text(text)
            if not compact:
                return False
            return compact.endswith((".", "!", "?", "함", "됨", "임", "다", "음"))
        last_block = prev_blocks[-1]
        last_tag = str(last_block.get("tag") or "")
        last_text = _normalize_requirement_text(str(last_block.get("text") or ""))
        if last_tag == "figcaption":
            carried.insert(0, prev_blocks.pop())
            if prev_blocks:
                prev_last = prev_blocks[-1]
                prev_tag = str(prev_last.get("tag") or "")
                prev_text = _normalize_requirement_text(str(prev_last.get("text") or ""))
                if (
                    prev_tag in {"p", "li", "div"}
                    and prev_text
                    and not _looks_complete(prev_text)
                ):
                    carried.insert(0, prev_blocks.pop())
        else:
            carried.insert(0, prev_blocks.pop())
        return carried

    for block in blocks:
        kind = "table" if block["tag"] == "table" else "body"
        if kind == "table" and segments and segments[-1][0] == "body" and segments[-1][1]:
            prev_kind, prev_blocks = segments[-1]
            carried_blocks = _carry_table_intro_blocks(prev_blocks)
            if not prev_blocks:
                segments.pop()
            segments.append(("table", [*carried_blocks, block]))
            continue

        if segments and segments[-1][0] == kind:
            segments[-1][1].append(block)
        else:
            segments.append((kind, [block]))

    if not segments:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    stage1_cards: list[RfpCard] = []
    parent_no = getattr(card, "card_no", None) or str(card.card_id)
    parent_page_idx = card.page_idx
    for idx, (kind, segment_blocks) in enumerate(segments, start=1):
        segment_html = "".join(block["html"] for block in segment_blocks).strip()
        segment_text = " ".join(block["text"] for block in segment_blocks).strip()
        if not segment_html:
            continue
        segment_subject = getattr(card, "subject", None) or card.requirement
        if kind == "body":
            candidate_subject = _normalize_requirement_text(str(segment_blocks[0].get("text") or ""))
            if candidate_subject and (
                _is_title_like_requirement_text(candidate_subject)
                or re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s*", candidate_subject, flags=re.IGNORECASE)
            ):
                segment_subject = candidate_subject
        stage1_cards.append(
            RfpCard(
                card_id=len(stage1_cards) + 1,
                card_no=f"{parent_no}-{idx}",
                requirement=card.requirement,
                subject=segment_subject,
                sub_subject="표" if kind == "table" else "본문",
                category=getattr(card, "category", None),
                part=getattr(card, "part", None) or "",
                section=card.section,
                html_excerpt=segment_html,
                page_idx=parent_page_idx,
                anchor=card.anchor,
            )
        )

    return stage1_cards or [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no)]


def _partition_card_for_requirement_build(card: RfpCard) -> list[RfpCard]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if not html_excerpt:
        return [card]

    blocks = _body_text_blocks(html_excerpt)
    if not blocks:
        return [card]

    has_table = any(block.get("tag") == "table" for block in blocks)
    has_body = any(block.get("tag") != "table" for block in blocks)
    if not (has_table and has_body):
        return [card]

    segments: list[tuple[str, list[dict]]] = []

    def _carry_table_intro_blocks(prev_blocks: list[dict]) -> list[dict]:
        carried: list[dict] = []
        if not prev_blocks:
            return carried
        def _looks_complete(text: str) -> bool:
            compact = _normalize_requirement_text(text)
            if not compact:
                return False
            return compact.endswith((".", "!", "?", "함", "됨", "임", "다", "음"))
        last_block = prev_blocks[-1]
        last_tag = str(last_block.get("tag") or "")
        last_text = _normalize_requirement_text(str(last_block.get("text") or ""))
        if last_tag == "figcaption":
            carried.insert(0, prev_blocks.pop())
            if prev_blocks:
                prev_last = prev_blocks[-1]
                prev_tag = str(prev_last.get("tag") or "")
                prev_text = _normalize_requirement_text(str(prev_last.get("text") or ""))
                if (
                    prev_tag in {"p", "li", "div"}
                    and prev_text
                    and not _looks_complete(prev_text)
                ):
                    carried.insert(0, prev_blocks.pop())
        else:
            carried.insert(0, prev_blocks.pop())
        return carried

    for block in blocks:
        kind = "table" if block["tag"] == "table" else "body"
        if kind == "table" and segments and segments[-1][0] == "body" and segments[-1][1]:
            carried_blocks = _carry_table_intro_blocks(segments[-1][1])
            if not segments[-1][1]:
                segments.pop()
            segments.append(("table", [*carried_blocks, block]))
            continue
        if kind == "table":
            segments.append((kind, [block]))
            continue
        if segments and segments[-1][0] == kind:
            segments[-1][1].append(block)
        else:
            segments.append((kind, [block]))

    split_cards: list[RfpCard] = []
    parent_no = getattr(card, "card_no", None) or str(card.card_id)
    for idx, (kind, segment_blocks) in enumerate(segments, start=1):
        segment_html = "".join(block["html"] for block in segment_blocks).strip()
        if not segment_html:
            continue
        body_fragment_level = 2 if kind == "body" and len(segment_blocks) > 1 else 1 if kind == "body" else None
        split_cards.append(
            RfpCard(
                card_id=len(split_cards) + 1,
                card_no=f"{parent_no}-rb{idx}",
                requirement=card.requirement,
                subject=getattr(card, "subject", None) or card.requirement,
                sub_subject="표" if kind == "table" else "본문",
                body_fragment_level=body_fragment_level,
                category=getattr(card, "category", None),
                part=getattr(card, "part", None) or "",
                section=card.section,
                html_excerpt=segment_html,
                page_idx=card.page_idx,
                anchor=card.anchor,
            )
        )
    return split_cards or [card]


def _row_block_name(card: RfpCard, item: dict, build_source: str) -> str:
    base = str(getattr(card, "card_no", None) or getattr(card, "card_id", "")).strip()
    if build_source != "표":
        return base
    try:
        table_index = int(item.get("table_index") or 0)
    except (TypeError, ValueError):
        table_index = 0
    if table_index <= 0:
        return base
    if re.search(r"-t\d+$", base):
        return base
    return f"{base}-t{table_index}"


def _split_parent_card_no(card_no: str | None) -> str:
    text = str(card_no or "").strip()
    if not text:
        return ""
    return re.sub(r"-rb\d+$", "", text)


def _plain_text_line_count(html_excerpt: str) -> int:
    plain_text = _plain_text_from_html_excerpt(html_excerpt)
    return len([line.strip() for line in plain_text.splitlines() if line.strip()])


def _inherits_requirement_id_from_previous_table(previous_card: RfpCard | None, current_card: RfpCard) -> bool:
    if previous_card is None:
        return False
    previous_kind = str(getattr(previous_card, "sub_subject", "") or "").strip()
    current_kind = str(getattr(current_card, "sub_subject", "") or "").strip()
    if not previous_kind.startswith("표") or current_kind != "본문":
        return False
    previous_parent = _split_parent_card_no(getattr(previous_card, "card_no", None))
    current_parent = _split_parent_card_no(getattr(current_card, "card_no", None))
    if not previous_parent or previous_parent != current_parent:
        return False
    return _plain_text_line_count(str(getattr(current_card, "html_excerpt", "") or "")) == 1


def _is_table_followup_common_note_card(previous_card: RfpCard | None, current_card: RfpCard) -> bool:
    if previous_card is None:
        return False
    previous_kind = str(getattr(previous_card, "sub_subject", "") or "").strip()
    current_kind = str(getattr(current_card, "sub_subject", "") or "").strip()
    if not previous_kind.startswith("표") or current_kind != "본문":
        return False
    previous_parent = _split_parent_card_no(getattr(previous_card, "card_no", None))
    current_parent = _split_parent_card_no(getattr(current_card, "card_no", None))
    if not previous_parent or previous_parent != current_parent:
        return False

    lines = [
        _normalize_requirement_text(line)
        for line in _plain_text_from_html_excerpt(str(getattr(current_card, "html_excerpt", "") or "")).splitlines()
        if _normalize_requirement_text(line)
    ]
    if not lines or len(lines) > 3:
        return False

    first_line = lines[0]
    if not re.match(r"^[\*※]\s*\S+", first_line):
        return False
    if any(
        re.match(
            r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.]|(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)[\)\.]|[IVXLCDM]+[\)\.])\s+",
            line,
            flags=re.IGNORECASE,
        )
        for line in lines
    ):
        return False
    return True


def _block_level_hint(block: dict) -> int | None:
    text = str(block.get("text") or "").strip()
    tag = str(block.get("tag") or "").lower()
    if not text:
        return None
    level = _detect_bullet_level(text)
    if level is not None:
        return level
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or _is_heading_like_text(text):
        return 1
    return None


def _split_body_card_by_indentation(card: RfpCard) -> list[RfpCard]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if not html_excerpt:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    card_no = str(getattr(card, "card_no", None) or card.card_id)
    if "-b" in card_no or "-t" in card_no or str(getattr(card, "sub_subject", "") or "").startswith(("본문", "표")):
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=card_no)]

    if "<table" in html_excerpt.lower():
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    blocks = [block for block in _body_text_blocks(html_excerpt) if block.get("tag") != "table"]
    if not blocks:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    levels = [lvl for lvl in (_block_level_hint(block) for block in blocks) if lvl is not None]
    if not levels:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    level_set = set(levels)
    bullet_levels = {lvl for lvl in levels if lvl in {2, 3, 4}}
    if not bullet_levels:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    if 3 in bullet_levels or 4 in bullet_levels:
        target_level = 2
    elif 2 in bullet_levels:
        target_level = 1
    else:
        target_level = 1

    segments: list[list[dict]] = []
    current_segment: list[dict] = []

    def _flush_segment() -> None:
        nonlocal current_segment
        if current_segment:
            segments.append(current_segment)
            current_segment = []

    for block in blocks:
        level = _block_level_hint(block)
        if level is None:
            if current_segment:
                current_segment.append(block)
            continue

        if level == 1 and not current_segment:
            current_segment = [block]
            continue

        if level <= target_level:
            _flush_segment()
            current_segment = [block]
            continue

        if current_segment:
            current_segment.append(block)

    _flush_segment()

    if not segments:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    split_cards: list[RfpCard] = []
    parent_no = card_no
    parent_page_idx = card.page_idx
    max_segments = 100
    for idx, segment_blocks in enumerate(segments, start=1):
        if len(split_cards) >= max_segments:
            break
        segment_html = "".join(block["html"] for block in segment_blocks).strip()
        segment_text = " ".join(block["text"] for block in segment_blocks).strip()
        if not segment_html:
            continue
        title_block = next((block for block in segment_blocks if _block_level_hint(block) is not None), segment_blocks[0])
        split_cards.append(
        RfpCard(
            card_id=len(split_cards) + 1,
                card_no=f"{parent_no}-b{idx}",
                requirement=card.requirement,
                subject=title_block["text"] or (getattr(card, "subject", None) or card.requirement),
                sub_subject=f"본문 {target_level}레벨",
                body_fragment_level=target_level,
                category=getattr(card, "category", None),
                part=getattr(card, "part", None) or "",
                section=card.section,
                html_excerpt=segment_html,
                page_idx=parent_page_idx,
                anchor=card.anchor,
            )
        )

    return split_cards or [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no)]


def _table_rows_to_matrix(table_tag: BeautifulSoup) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table_tag.find_all("tr", recursive=False):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        row = [_cell_text_compact(cell) for cell in cells]
        if any(row):
            rows.append(row)
    return rows


def _table_visual_matrix(
    table_tag: BeautifulSoup,
    preserve_breaks: bool = False,
    strip_nested_tables: bool = False,
) -> list[list[str]]:
    tr_nodes = [tr for tr in table_tag.find_all("tr") if tr.find_parent("table") is table_tag]
    if not tr_nodes:
        tr_nodes = table_tag.find_all("tr")
    if not tr_nodes:
        return []

    grid: dict[tuple[int, int], str] = {}
    max_row = 0
    max_col = 0
    for r_idx, tr in enumerate(tr_nodes):
        c_idx = 0
        while (r_idx, c_idx) in grid:
            c_idx += 1
        for cell in tr.find_all(["th", "td"], recursive=False):
            while (r_idx, c_idx) in grid:
                c_idx += 1
            if strip_nested_tables:
                text = _cell_text_without_nested_tables(cell, preserve_breaks=preserve_breaks)
            else:
                text = _cell_text_preserve_breaks(cell) if preserve_breaks else _cell_text_compact(cell)
            try:
                rowspan = max(int(cell.get("rowspan", 1) or 1), 1)
            except (TypeError, ValueError):
                rowspan = 1
            try:
                colspan = max(int(cell.get("colspan", 1) or 1), 1)
            except (TypeError, ValueError):
                colspan = 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(r_idx + dr, c_idx + dc)] = text
            max_row = max(max_row, r_idx + rowspan)
            max_col = max(max_col, c_idx + colspan)
            c_idx += colspan

    matrix: list[list[str]] = []
    for r in range(max_row):
        row = [grid.get((r, c), "") for c in range(max_col)]
        if any(row):
            matrix.append(row)
    return matrix


def _matrix_to_table_html(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    max_cols = max(len(row) for row in rows)
    table_rows: list[str] = []
    for row_idx, row in enumerate(rows):
        tag_name = "th" if row_idx == 0 else "td"
        cells = "".join(f"<{tag_name}>{escape((cell or '').strip())}</{tag_name}>" for cell in row)
        cells += "".join(f"<{tag_name}></{tag_name}>" for _ in range(max_cols - len(row)))
        table_rows.append(f"<tr>{cells}</tr>")
    return "<table>" + "".join(table_rows) + "</table>"


def _table_rows_to_original_html(table_tag: BeautifulSoup) -> list[str]:
    row_html: list[str] = []
    for tr in table_tag.find_all("tr"):
        if tr.find_parent("table") is table_tag:
            row_html.append(str(tr))
    return row_html


def _table_row_records(table_tag: BeautifulSoup) -> list[tuple[str, list[str]]]:
    records: list[tuple[str, list[str]]] = []
    rows = table_tag.find_all("tr", recursive=False)
    if not rows:
        rows = table_tag.find_all("tr", recursive=True)
    for tr in rows:
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        values = [_cell_text_compact(cell) for cell in cells]
        if any(values):
            records.append((str(tr), values))
    return records


def _table_row_records_linewise(table_tag: BeautifulSoup) -> list[tuple[str, list[str]]]:
    records: list[tuple[str, list[str]]] = []
    rows = table_tag.find_all("tr", recursive=False)
    if not rows:
        rows = table_tag.find_all("tr", recursive=True)
    for tr in rows:
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        values: list[str] = []
        for cell in cells:
            line_values = [
                _normalize_requirement_text(line)
                for line in _extract_lines_from_tag(cell)
                if _normalize_requirement_text(line)
            ]
            if line_values:
                values.append("\n".join(line_values))
                continue
            values.append(_cell_text_preserve_breaks(cell) or _cell_text_compact(cell))
        if any(_normalize_requirement_text(value) for value in values):
            records.append((str(tr), values))
    return records


def _table_row_key(tr: BeautifulSoup, boundary_col: int) -> str:
    cells = tr.find_all(["th", "td"], recursive=False)
    if not cells:
        return ""
    if boundary_col < 0:
        boundary_col = 0
    idx = min(boundary_col, len(cells) - 1)
    return _cell_text_compact(cells[idx])


def _table_has_span(table_tag: BeautifulSoup) -> bool:
    for cell in table_tag.find_all(["th", "td"]):
        rowspan = str(cell.get("rowspan", "1") or "1")
        colspan = str(cell.get("colspan", "1") or "1")
        if rowspan != "1" or colspan != "1":
            return True
    return False


def _table_has_nested_table(table_tag: BeautifulSoup) -> bool:
    for nested in table_tag.find_all("table"):
        if nested is not table_tag:
            return True
    return False


def _infer_table_boundary_col(matrix_rows: list[list[str]]) -> int:
    if not matrix_rows:
        return 0
    max_cols = max(len(row) for row in matrix_rows)
    for col_idx in range(max_cols - 1, -1, -1):
        if all(col_idx < len(row) and str(row[col_idx]).strip() for row in matrix_rows):
            return col_idx
    for col_idx in range(max_cols):
        if any(col_idx < len(row) and str(row[col_idx]).strip() for row in matrix_rows):
            return col_idx
    return 0


def _build_table_html_from_rows(table_tag: BeautifulSoup, row_html_list: list[str]) -> str:
    if not row_html_list:
        return ""
    soup = BeautifulSoup(str(table_tag), "html.parser")
    table_copy = soup.find("table")
    if table_copy is None:
        return "<table>" + "".join(row_html_list) + "</table>"

    preserved_children: list[str] = []
    for child in list(table_copy.children):
        name = getattr(child, "name", None)
        if name in {"caption", "colgroup", "thead", "tfoot"}:
            preserved_children.append(str(child))

    table_copy.clear()
    for child_html in preserved_children:
        child_soup = BeautifulSoup(child_html, "html.parser")
        child_tag = child_soup.find(True)
        if child_tag is not None:
            table_copy.append(child_tag)

    tbody = soup.new_tag("tbody")
    for row_html in row_html_list:
        row_soup = BeautifulSoup(row_html, "html.parser")
        row_tag = row_soup.find("tr")
        if row_tag is not None:
            tbody.append(row_tag)
    if tbody.contents:
        table_copy.append(tbody)
    return str(table_copy)


def _build_table_html_from_matrix_rows(table_tag: BeautifulSoup, matrix_rows: list[list[str]]) -> str:
    if not matrix_rows:
        return ""

    soup = BeautifulSoup(str(table_tag), "html.parser")
    table_copy = soup.find("table")
    if table_copy is None:
        table_copy = soup.new_tag("table")

    preserved_children: list[str] = []
    for child in list(table_copy.children):
        name = getattr(child, "name", None)
        if name in {"caption", "colgroup", "thead", "tfoot"}:
            preserved_children.append(str(child))

    table_copy.clear()
    for child_html in preserved_children:
        child_soup = BeautifulSoup(child_html, "html.parser")
        child_tag = child_soup.find(True)
        if child_tag is not None:
            table_copy.append(child_tag)

    first_tr = table_tag.find("tr")
    first_has_th = bool(first_tr and first_tr.find("th"))
    tbody = soup.new_tag("tbody")

    def _append_cell_content(cell_tag, value: object) -> None:
        text = "" if value is None else str(value)
        if "\n" not in text and "\r" not in text:
            cell_tag.string = text
            return
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        for idx, line in enumerate(lines):
            if idx:
                cell_tag.append(soup.new_tag("br"))
            if line:
                cell_tag.append(line)

    for row_idx, row_values in enumerate(matrix_rows):
        tr = soup.new_tag("tr")
        cell_name = "th" if row_idx == 0 and first_has_th else "td"
        for value in row_values:
            cell = soup.new_tag(cell_name)
            _append_cell_content(cell, value)
            tr.append(cell)
        tbody.append(tr)
    if tbody.contents:
        table_copy.append(tbody)
    return str(table_copy)


def _normalize_nested_tables_in_html(html_fragment: str) -> str:
    # Preserve nested table markup exactly as it appeared in the source HTML.
    # For table splitting we may ignore nested tables while inferring row
    # boundaries, but once a parent row range is selected the nested table
    # content itself must remain untouched.
    return html_fragment


def _is_header_only_split_table_html(table_html: str) -> bool:
    if "<table" not in (table_html or "").lower():
        return False
    soup = BeautifulSoup(table_html, "html.parser")
    table = soup.find("table")
    if table is None:
        return False
    matrix = _table_visual_matrix(table, preserve_breaks=False)
    if len(matrix) <= 1:
        return False

    def _norm_row(row: list[str]) -> list[str]:
        return [re.sub(r"\s+", " ", str(cell or "")).strip().lower() for cell in row]

    header = _norm_row(matrix[0])
    data_rows = [_norm_row(row) for row in matrix[1:] if any(str(cell or "").strip() for cell in row)]
    if not data_rows:
        return False
    return all(row == header for row in data_rows)


def _split_table_rows_by_boundary_value(matrix_rows: list[list[str]]) -> list[list[int]]:
    if not matrix_rows:
        return []
    boundary_col = _infer_table_boundary_col(matrix_rows)
    if len(matrix_rows) <= 1:
        return [[idx + 1 for idx in range(len(matrix_rows))]]

    groups: list[list[int]] = []
    current_rows: list[int] = [1]
    current_key: str | None = None
    for row_idx, row_values in enumerate(matrix_rows[1:], start=2):
        key = row_values[boundary_col].strip() if boundary_col < len(row_values) else ""
        if current_key is None:
            current_key = key
        elif key and key != current_key and current_rows:
            groups.append(current_rows)
            current_rows = [1]
            current_key = key
        current_rows.append(row_idx)
    if current_rows:
        groups.append(current_rows)
    return groups


def _partition_table_cards_by_columns(card: RfpCard) -> list[RfpCard]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if not html_excerpt:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    card_no = str(getattr(card, "card_no", None) or card.card_id)
    if "-t" in card_no:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=card_no)]

    title_candidates = [
        str(getattr(card, "subject", None) or "").strip(),
        str(card.requirement or "").strip(),
    ]
    context_title_candidates = [
        *title_candidates,
        str(getattr(card, "section", None) or "").strip(),
    ]
    force_split_keywords = ("요건", "요구", "요청", "이행")
    force_split = any(keyword in title for title in title_candidates for keyword in force_split_keywords if title)
    if not force_split:
        split_excluded_keywords = ("서식", "현황", "서류", "유의사항", "일정", "제출", "담당자")
        if any(keyword in title for title in title_candidates for keyword in split_excluded_keywords if title):
            return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=card_no)]
        if any(("당사" in title and "표준" in title) for title in context_title_candidates if title):
            return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=card_no)]
        if any(("당사" in title and "시스템" in title) for title in context_title_candidates if title):
            return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=card_no)]

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    if not tables:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    split_cards: list[RfpCard] = []
    parent_no = card_no
    parent_page_idx = card.page_idx
    table_counter = 0
    max_tables = 100
    for idx, table in enumerate(tables, start=1):
        if table_counter >= max_tables:
            break
        row_html_list = _table_rows_to_original_html(table)
        has_nested_table = _table_has_nested_table(table)
        matrix = _table_visual_matrix(table, preserve_breaks=True, strip_nested_tables=has_nested_table)
        if not matrix:
            table_counter += 1
            split_cards.append(
                RfpCard(
                    card_id=len(split_cards) + 1,
                    card_no=f"{parent_no}-t{table_counter}",
                    requirement=card.requirement,
                    subject=getattr(card, "subject", None) or card.requirement,
                    sub_subject="표(병합셀 포함)" if _table_has_span(table) else "표",
                    category=getattr(card, "category", None),
                    part=getattr(card, "part", None) or "",
                    section=card.section,
                    html_excerpt=str(table).strip(),
                    page_idx=parent_page_idx,
                    anchor=card.anchor,
                )
            )
            continue

        max_cols = max(len(row) for row in matrix) if matrix else 0
        if max_cols <= 1 or len(matrix) <= 1 or not row_html_list:
            table_counter += 1
            split_cards.append(
                RfpCard(
                    card_id=len(split_cards) + 1,
                    card_no=f"{parent_no}-t{table_counter}",
                    requirement=card.requirement,
                    subject=getattr(card, "subject", None) or card.requirement,
                    sub_subject="표(병합셀 포함)" if _table_has_span(table) else "표",
                    category=getattr(card, "category", None),
                    part=getattr(card, "part", None) or "",
                    section=card.section,
                    html_excerpt=str(table).strip(),
                    page_idx=parent_page_idx,
                    anchor=card.anchor,
                )
            )
            continue

        groups = _split_table_rows_by_boundary_value(matrix)

        for group_row_indices in groups:
            if not group_row_indices:
                continue
            table_counter += 1
            if has_nested_table:
                split_row_html = [
                    row_html_list[row_idx - 1]
                    for row_idx in group_row_indices
                    if 1 <= row_idx <= len(row_html_list)
                ]
                split_table_html = _normalize_nested_tables_in_html(_build_table_html_from_rows(table, split_row_html))
            else:
                split_matrix_rows = [
                    matrix[row_idx - 1]
                    for row_idx in group_row_indices
                    if 1 <= row_idx <= len(matrix)
                ]
                split_table_html = _build_table_html_from_matrix_rows(table, split_matrix_rows)
            if _is_header_only_split_table_html(split_table_html):
                continue
            split_cards.append(
                RfpCard(
                    card_id=len(split_cards) + 1,
                    card_no=f"{parent_no}-t{table_counter}",
                    requirement=card.requirement,
                    subject=getattr(card, "subject", None) or card.requirement,
                    sub_subject="표(병합셀 포함)" if _table_has_span(table) else "표",
                    category=getattr(card, "category", None),
                    part=getattr(card, "part", None) or "",
                    section=card.section,
                    html_excerpt=split_table_html,
                    page_idx=parent_page_idx,
                    anchor=card.anchor,
                )
            )

    return split_cards or [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no)]


def _build_excerpt_from_parent(parent_html: str, start_title: str, end_title: str | None = None) -> str:
    blocks = _body_text_blocks(parent_html)
    if not blocks:
        return ""
    start_idx = _find_title_start_index(blocks, start_title, 0)
    if start_idx is None:
        return ""
    if end_title:
        end_idx = _find_title_start_index(blocks, end_title, start_idx + 1)
        if end_idx is None or end_idx <= start_idx:
            return ""
    else:
        end_idx = len(blocks)
    return "".join(block["html"] for block in blocks[start_idx:end_idx]).strip()


def _find_text_start_index(body_blocks: list[dict], text_value: str, start_from: int = 0) -> int | None:
    needle = re.sub(r"\s+", " ", (text_value or "")).strip()
    if not needle:
        return None
    needle_key = _title_key(needle)
    for idx in range(max(start_from, 0), len(body_blocks)):
        text = re.sub(r"\s+", " ", body_blocks[idx]["text"]).strip()
        if not text:
            continue
        if needle in text:
            return idx
        text_key = _title_key(text)
        if needle_key and needle_key in text_key:
            return idx
    return None


def _build_excerpt_from_parent_text(parent_html: str, start_text: str, end_text: str | None = None) -> str:
    blocks = _body_text_blocks(parent_html)
    if not blocks or not start_text:
        return ""
    start_idx = _find_text_start_index(blocks, start_text, 0)
    if start_idx is None:
        return ""
    if end_text:
        end_idx = _find_text_start_index(blocks, end_text, start_idx + 1)
        if end_idx is None or end_idx <= start_idx:
            end_idx = len(blocks)
    else:
        end_idx = len(blocks)
    return "".join(block["html"] for block in blocks[start_idx:end_idx]).strip()


def _split_parent_excerpt_sequentially(parent_html: str, items: list[dict]) -> list[str]:
    blocks = _body_text_blocks(parent_html)
    if not blocks or not items:
        return []

    positions: list[int] = []
    cursor = 0
    for item in items:
        start_text = str(item.get("start_text") or item.get("subject") or item.get("requirement") or "").strip()
        if not start_text:
            return []
        start_idx = _find_text_start_index(blocks, start_text, cursor)
        if start_idx is None:
            return []
        positions.append(start_idx)
        cursor = start_idx + 1

    excerpts: list[str] = []
    for i, item in enumerate(items):
        start_idx = positions[i]
        next_start = positions[i + 1] if i + 1 < len(positions) else None
        end_text = str(item.get("end_text") or "").strip()

        end_idx = None
        if next_start is not None and next_start > start_idx:
            end_idx = next_start
        elif end_text:
            end_idx = _find_text_start_index(blocks, end_text, start_idx + 1)
            if end_idx is not None and end_idx <= start_idx:
                end_idx = None
        if end_idx is None:
            end_idx = len(blocks)

        excerpts.append("".join(block["html"] for block in blocks[start_idx:end_idx]).strip())
    return excerpts


def _split_parent_excerpt_match_status(parent_html: str, items: list[dict]) -> list[dict]:
    blocks = _body_text_blocks(parent_html)
    if not blocks or not items:
        return []

    statuses: list[dict] = []
    cursor = 0
    for item in items:
        start_text = str(item.get("start_text") or item.get("subject") or item.get("requirement") or "").strip()
        end_text = str(item.get("end_text") or "").strip()
        start_idx = _find_text_start_index(blocks, start_text, cursor) if start_text else None
        matched = start_idx is not None
        if matched:
            cursor = start_idx + 1
        statuses.append(
            {
                "start_text": start_text,
                "end_text": end_text,
                "matched": matched,
                "start_index": start_idx,
            }
        )
    return statuses


def _split_cards_via_llm(
    cards: list[RfpCard],
    model_name: str,
    progress_update: Callable[[int, int, str], None] | None = None,
) -> list[RfpCard]:
    api_key = _load_openai_api_key()
    if not api_key:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id)) for card in cards]
    try:
        from openai import OpenAI
    except Exception:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id)) for card in cards]

    client = OpenAI(api_key=api_key)
    split_cards: list[RfpCard] = []
    split_debug: dict[str, dict] = {}

    system_prompt = (
        "You split one parent card only under these three cases: "
        "1) the body contains bullets at the same level as the requirement, or a lower level, or a higher level, and those bullets indicate separate requirements; "
        "2) the body contains an additional lower layer that should become its own requirement card; "
        "3) the body contains a table, in which case you must identify the table title and split based on the table hierarchy, using the level just above the last level as the split boundary. "
        "Do not split for any other reason. "
        "Do not rely on HTML tags alone. "
        "Do not split because bullets exist unless they clearly represent separate requirements. "
        "Do not split because a table is long unless the table hierarchy shows a split boundary. "
        "Keep explanatory bullets with the requirement they support. "
        "If the card is already one clear requirement, return exactly one item unchanged. "
        "Return JSON array only. Each item must include: parent_card_no, card_no, requirement, subject, sub_subject, group, section, page_idx, anchor, start_text, end_text. "
        "Do not include html_excerpt in the LLM output. "
        "The start_text and end_text must be real text copied from the parent content and must be long enough to avoid ambiguity. "
        "Prefer a longer text that includes the title plus surrounding words, the first bullet text, the table header, or an adjacent phrase. "
        "Do not make the text a single short title if a longer unique phrase exists nearby. "
        "The text should be distinctive enough that a rule-based search can find it without confusion. "
        "If the split item runs to the end of the parent excerpt, set end_text to an empty string. "
        "When split, use parent-child numbering such as 3-1, 3-2, 3-1-1. "
        "Requirement must remain the parent requirement. subject is the new split title. sub_subject is only for a second split. "
        "Preserve document order. Do not invent content outside the supplied parent card text. "
    )

    for card in cards:
        parent_no = getattr(card, "card_no", None) or str(card.card_id)
        parent_page_idx = card.page_idx
        if progress_update:
            progress_update(len(split_cards), len(cards), f"처리 중: 카드 {parent_no}")
        payload = {
            "parent_card": _split_card_payload(card),
            "analysis_context": _split_card_context(card),
            "instruction": (
                "Return only the final split cards. "
                "Apply only these three split cases: "
                "1) same-level, upper-level, or lower-level bullets in the body when they indicate separate requirements; "
                "2) an additional lower layer in the body that should become its own requirement card; "
                "3) tables, where you must extract the table title, inspect the table hierarchy, and split at the level just above the last level. "
                "Do not split for any other reason. "
                "If no split is needed, return one unchanged card. "
                "Keep explanatory bullets together. "
                "Return only start_text and end_text for each split card. Do not return html_excerpt. "
                "The code will cut html_excerpt from the parent card using those text boundaries. "
                "Make the text values long and specific enough that a rule-based search can find them reliably. "
                "Requirement stays as the parent requirement. subject is the new split title. sub_subject is only for a second split."
            ),
        }
        try:
            response = client.responses.create(
                model=model_name or "gpt-4o",
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            )
            content = (getattr(response, "output_text", None) or "").strip()
            split_debug[parent_no] = {
                "parent_card_no": parent_no,
                "input_title": getattr(card, "subject", None) or card.requirement,
                "raw_response": content,
                "input_excerpt": str(card.html_excerpt or "")[:5000],
            }
            if not content:
                split_cards.append(_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no))
                continue
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                match = re.search(r"\[[\s\S]*\]", content)
                if not match:
                    split_cards.append(_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no))
                    continue
                parsed = json.loads(match.group(0))
            if not isinstance(parsed, list) or not parsed:
                split_cards.append(_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no))
                continue
            match_statuses = _split_parent_excerpt_match_status(str(card.html_excerpt or ""), parsed)
            split_debug[parent_no]["match_statuses"] = match_statuses
            split_excerpts = _split_parent_excerpt_sequentially(str(card.html_excerpt or ""), parsed)
            if not split_excerpts:
                split_cards.append(_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no))
                continue
            for child_idx, item in enumerate(parsed, start=1):
                if not isinstance(item, dict):
                    continue
                subject = str(item.get("subject") or item.get("requirement") or card.requirement).strip()
                if not subject:
                    subject = card.requirement
                sub_subject = str(item.get("sub_subject") or "").strip()
                card_no = str(item.get("card_no") or f"{parent_no}-{child_idx}")
                split_excerpt = split_excerpts[child_idx - 1] if child_idx - 1 < len(split_excerpts) else ""
                if not split_excerpt:
                    split_excerpt = str(item.get("html_excerpt") or "").strip()
                split_cards.append(
                    RfpCard(
                        card_id=len(split_cards) + 1,
                        card_no=card_no,
                        requirement=str(item.get("requirement") or card.requirement).strip() or card.requirement,
                        subject=subject,
                        sub_subject=sub_subject or None,
                        category=getattr(card, "category", None),
                        part=str(item.get("part") or getattr(card, "part", None) or "").strip(),
                        section=str(item.get("section") or card.section).strip() or card.section,
                        html_excerpt=split_excerpt,
                        page_idx=parent_page_idx,
                        anchor=str(item.get("anchor") or card.anchor or "").strip() or card.anchor,
                    )
                )
        except Exception:
            split_cards.append(_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no))
    st.session_state["cards_step2_split_debug"] = split_debug

    if progress_update:
        progress_update(len(split_cards), len(cards), "분리 완료")
    return split_cards


def _run_step3(
    keep_artifacts: bool,
    mark_running: Callable[[str, str], None],
    mark_done: Callable[[str, str], None],
    step_key: str = "step3",
) -> None:
    use_merged_html = str(st.session_state.get("step4_html_source_mode") or "merged").strip().lower() != "raw"
    if use_merged_html:
        html = st.session_state.get("html_raw_merged_empty_up_postprocessed") or ""
        st.session_state["step3_html_source"] = "html_raw_merged_empty_up_postprocessed" if html else ""
    else:
        html = st.session_state.get("html_raw") or ""
        st.session_state["step3_html_source"] = "html_raw" if html else ""
    st.session_state["step4_generated_html_source"] = st.session_state.get("step3_html_source") or ""
    toc_items = st.session_state["saved_toc_items"]
    if not html:
        if use_merged_html:
            st.warning("먼저 2단계에서 연속표 병합 후 후처리본 HTML을 생성해주세요.")
        else:
            st.warning("먼저 1단계에서 `html_raw`를 생성해주세요.")
        return
    if toc_items is None:
        st.warning("먼저 2단계에서 편집한 목차를 저장해주세요.")
        return
    if not toc_items:
        st.warning("최종 목차가 0건이라 카드 생성을 진행할 수 없습니다. 목차를 저장하거나 다시 불러와 주세요.")
        return

    mark_running(step_key, "목차 기반 조견표 카드를 생성하는 중")
    sections = _build_sections_from_final_toc(html, toc_items)
    cards = _build_cards_from_sections(sections)
    st.session_state["cards_step2"] = cards
    st.session_state["cards_step2_signature"] = _section_requirement_cards_signature(cards)
    _clear_section_requirement_cache()
    mark_done(step_key, f"조견표 카드 {len(cards)}건 생성 완료")

    if keep_artifacts and st.session_state["file_name"]:
        artifact_root = current_artifact_root()
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "toc.json").write_text(
            json.dumps([item.__dict__ for item in toc_items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (artifact_root / "cards.json").write_text(
            json.dumps([card.__dict__ for card in cards], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        st.success(f"산출물을 {artifact_root} 에 저장했습니다.")

    st.success(f"조견표 카드 {len(cards)}건을 생성했습니다.")


_LLM_MODEL_PRICING_PER_1M = {
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-5.2": {"input": 1.75, "output": 14.00},
    "gpt-5.4": {"input": 1.75, "output": 14.00},
    "gpt-5.5": {"input": 1.75, "output": 14.00},
}


def _extract_token_usage(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        return int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0), int(
            usage.get("output_tokens") or usage.get("completion_tokens") or 0
        )
    return int(getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0), int(
        getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0
    )


def _estimate_llm_cost_usd(model_name: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _LLM_MODEL_PRICING_PER_1M.get(model_name)
    if not pricing:
        return 0.0
    return (input_tokens / 1_000_000.0) * pricing["input"] + (output_tokens / 1_000_000.0) * pricing["output"]


def _responses_create_kwargs(model_name: str) -> dict:
    kwargs: dict = {}
    if not str(model_name or "").startswith("gpt-5"):
        kwargs["temperature"] = 0
    return kwargs


def _extract_json_payload(content: str) -> object:
    content = (content or "").strip()
    if not content:
        raise RuntimeError("LLM 응답이 비어 있습니다.")
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", content).strip()
        if content.endswith("```"):
            content = content[:-3].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", content)
        if not match:
            raise
        return json.loads(match.group(1))


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


_HEADER_LIKE_TERMS = {
    "표",
    "본문",
    "내용",
    "구분",
    "항목",
    "항목명",
    "상세내역",
    "상세요건",
    "상세내용",
    "세부내역",
    "세부내용",
    "요구사항",
}


def _is_header_like_field(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    # 표/본문의 실제 항목 본문에 불릿이나 번호가 붙어 있으면
    # 헤더로 오인하지 않도록 강제적으로 header-like 판정을 제외한다.
    if _has_explicit_bullet_marker_text(normalized) or _has_quoted_bullet_marker(normalized):
        return False

    compact = re.sub(r"[\s\-\_/,:;()\[\]{}]+", "", normalized)
    if compact in _HEADER_LIKE_TERMS:
        return True

    parts = [
        re.sub(r"[\s\-\_/,:;()\[\]{}]+", "", part)
        for part in re.split(r"[\n|]+", normalized)
    ]
    parts = [part for part in parts if part]
    if not parts:
        return False
    if all(part in _HEADER_LIKE_TERMS for part in parts):
        return True

    token_hits = sum(1 for term in _HEADER_LIKE_TERMS if term in compact)
    has_only_header_chars = re.fullmatch(r"[가-힣A-Za-z]+", compact or "") is not None
    return token_hits >= 2 and len(compact) <= 14 and has_only_header_chars


def _is_header_like_requirement_row(item_name: str, requirement: str, detail_requirement: str, result_note: str = "") -> bool:
    fields = [item_name, requirement, detail_requirement]
    if any(
        _has_explicit_bullet_marker_text(field) or _has_quoted_bullet_marker(field)
        for field in fields
    ):
        return False
    header_like_count = sum(1 for field in fields if _is_header_like_field(field))
    if header_like_count >= 2:
        return True

    non_empty_fields = [field for field in fields if _normalize_requirement_text(field)]
    if non_empty_fields and all(_is_header_like_field(field) for field in non_empty_fields):
        return True

    if result_note and _is_header_like_field(result_note) and header_like_count >= 1:
        return True
    return False


def _is_redundant_same_text_requirement_row(item_name: str, requirement: str, detail_requirement: str) -> bool:
    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)
    normalized_detail = _normalize_requirement_text(detail_requirement)
    if not normalized_item or not normalized_requirement or not normalized_detail:
        return False
    return normalized_item == normalized_requirement == normalized_detail


def _is_redundant_requirement_detail_pair(requirement: str, detail_requirement: str) -> bool:
    normalized_requirement = _normalize_requirement_text(requirement)
    normalized_detail = _normalize_requirement_text(detail_requirement)
    if not normalized_requirement or not normalized_detail:
        return False
    if normalized_requirement == normalized_detail:
        return True

    stripped_requirement = _strip_leading_numbering(normalized_requirement)
    if stripped_requirement and stripped_requirement == normalized_detail:
        return True

    stripped_korean_requirement = re.sub(r"^\s*[가-하ㄱ-ㅎ][\.\)]\s*", "", normalized_requirement).strip()
    if stripped_korean_requirement and stripped_korean_requirement == normalized_detail:
        return True

    return False


def _detail_dedup_key_text(value: str) -> str:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return ""
    # Keep the leading numbering / bullet marker here.
    # These prefixes can distinguish separate hierarchical items such as
    # "(9.3)" and "(9.10)", which must not collapse into one row.
    return normalized


def _row_consecutive_dedup_key(
    card_no: str,
    source_detail_label: str,
    item_name: str,
    requirement: str,
    detail_requirement: str,
    result_note: str,
) -> tuple[str, str, str, str, str, str]:
    return (
        _normalize_requirement_text(card_no),
        _normalize_requirement_text(source_detail_label),
        _normalize_requirement_text(item_name),
        _normalize_requirement_text(requirement),
        _detail_dedup_key_text(detail_requirement),
        _normalize_requirement_text(result_note),
    )


def _strip_trailing_orphan_bullet(value: str, source_tag: str = "") -> str:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return ""

    # Avoid catastrophic backtracking on OCR-heavy dash runs such as
    # "------------------------------------------------ 23.4 ~ 23.6".
    bullet_only_pattern = re.compile(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*\s]+$")
    cleaned_lines: list[str] = []
    for line in normalized.split("\n"):
        compact_line = _normalize_requirement_text(line)
        if not compact_line:
            continue
        if bullet_only_pattern.fullmatch(compact_line):
            continue
        cleaned_lines.append(compact_line)

    if not cleaned_lines:
        return ""

    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"\s+[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+$", "", cleaned).strip()
    if str(source_tag or "").lower() == "li":
        if bullet_only_pattern.fullmatch(cleaned):
            return ""
        return cleaned
    if str(source_tag or "").lower() not in {"본문", "body"}:
        # 문장 끝에 붙은 "가.", "나)", "ㄱ." 같은 잔여 마커는 불릿이 아니라
        # OCR/줄바꿈 잔상으로 보고 제거한다.
        cleaned = re.sub(
            r"\s+(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]\s*$",
            "",
            cleaned,
        ).strip()
    cleaned = re.sub(r"[,\u3001\uFF0C]\s*$", "", cleaned).strip()
    if bullet_only_pattern.fullmatch(cleaned):
        return ""
    return cleaned


def _normalize_middle_dot_connectors(value: str) -> str:
    text = _normalize_requirement_text(value)
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _has_inline_middle_dot_connector(value: str) -> bool:
    text = _normalize_requirement_text(value)
    if not text:
        return False
    return bool(re.search(r"(?<=\S)\s*[·ㆍ•◦]\s*(?=\S)", text))


def _split_inline_standalone_bullet_units(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []
    if _looks_like_leading_bullet_connector_phrase(normalized):
        return [normalized]
    if _looks_like_dash_connected_term_chain(normalized):
        return [normalized]
    if _looks_like_asterisk_quantity_expression(normalized):
        return [normalized]
    pattern = re.compile(r"\s+(?P<marker>[\-*▪■◆▶○□◇⦁−–—]+)\s+(?=\S)")
    match = pattern.search(normalized)
    if not match:
        return [normalized]
    left = _normalize_requirement_text(normalized[:match.start()])
    # 뒤쪽 조각은 marker를 유지해야 다음 재귀에서 또 다른 불릿으로 판정할 수 있다.
    right = _normalize_requirement_text(normalized[match.start():])
    units: list[str] = []
    if left:
        units.extend(_split_inline_standalone_bullet_units(left))
    if right:
        units.extend(_split_inline_standalone_bullet_units(right))
    return units or [normalized]


def _looks_like_asterisk_quantity_expression(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized or "\n" in normalized:
        return False
    if " * " not in normalized:
        return False
    return bool(
        re.search(
            r"\b[가-힣A-Za-z0-9/()_-]{1,30}\s+\*\s+(?:\d+|[0-9]+(?:EA|개|식|대|포트|GB|TB|U)?)\b",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _split_repeated_bullet_chain(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []
    if _is_reference_like_body_line(normalized):
        return [normalized]
    # 표 안에서 반복되는 불릿 체인은 각 불릿을 별도 상세요건으로 분리한다.
    bullet_pattern = re.compile(r"(?=(?:^|\s)(?:[•▪■◆▶◦○□◇⦁])\s+\S)")
    hits = list(bullet_pattern.finditer(normalized))
    if len(hits) <= 1:
        return [normalized]
    pieces: list[str] = []
    starts = [hit.start() for hit in hits]
    starts.append(len(normalized))
    for idx in range(len(starts) - 1):
        chunk = _normalize_requirement_text(normalized[starts[idx]:starts[idx + 1]])
        if chunk:
            pieces.append(chunk)
    return pieces or [normalized]


def _has_quoted_bullet_marker(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    quoted_patterns = [
        r"『[^』]*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^』]*?』",
        r"「[^」]*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^」]*?」",
        r"“[^”]*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^”]*?”",
        r"\"[^\"]*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^\"]*?\"",
        r"'[^']*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^']*?'",
    ]
    return any(re.search(pattern, normalized) for pattern in quoted_patterns)


def _looks_like_connected_dash_phrase(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized or "\n" in normalized:
        return False
    separator_pattern = r"(?:->|→|[-–—])"
    if not re.search(rf"\s{separator_pattern}\s", normalized):
        return False
    if re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return False

    parts = [part.strip() for part in re.split(r"\s*(?:->|→|[-–—])\s*", normalized) if part.strip()]
    if len(parts) < 2 or len(parts) > 4:
        return False
    if any(
        re.match(
            r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
            part,
            flags=re.IGNORECASE,
        )
        for part in parts
    ):
        return False
    if _looks_like_sentence_text(normalized):
        return False
    return all(len(part) <= 20 for part in parts)


def _looks_like_leading_bullet_connector_phrase(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized or "\n" in normalized:
        return False
    if not re.match(r"^\s*[\-*▪■◆▶◦○□◇⦁·ㆍ−–—]+\s+\S", normalized):
        return False
    body = re.sub(r"^\s*[\-*▪■◆▶◦○□◇⦁·ㆍ−–—]+\s+", "", normalized, count=1).strip()
    if not body:
        return False
    if not re.search(r"\s(?:->|→|[-–—])\s", body):
        return False
    if re.search(
        r"(?:^|\s)(?:[•▪■◆▶◦○□◇⦁\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        body,
        flags=re.IGNORECASE,
    ):
        return False
    return True


def _looks_like_dash_connected_term_chain(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized or "\n" in normalized:
        return False
    separator_pattern = r"(?:->|→|[-–—])"
    if not re.search(rf"\s{separator_pattern}\s", normalized):
        return False
    if re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return False

    parts = [part.strip() for part in re.split(r"\s*(?:->|→|[-–—])\s*", normalized) if part.strip()]
    if len(parts) < 2 or len(parts) > 5:
        return False
    if any(
        re.match(
            r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
            part,
            flags=re.IGNORECASE,
        )
        for part in parts
    ):
        return False

    def _looks_like_term(part: str, *, allow_suffix: bool = False) -> bool:
        compact = _normalize_requirement_text(part)
        if not compact:
            return False
        if re.search(r"[,:;]", compact):
            return False
        if allow_suffix:
            return bool(
                re.fullmatch(
                    r"[가-힣A-Za-z0-9/()]{1,20}(?:\s+[가-힣A-Za-z0-9/()]{1,20}){0,5}(?:까지|까지의|연결|흐름|단계|절차|프로세스|기능|처리|연계|전환|구성|방식)?",
                    compact,
                )
            )
        return bool(
            re.fullmatch(
                r"[가-힣A-Za-z0-9/()]{1,20}(?:\s+[가-힣A-Za-z0-9/()]{1,20}){0,2}",
                compact,
            )
        )

    if not all(_looks_like_term(part) for part in parts[:-1]):
        return False
    if not _looks_like_term(parts[-1], allow_suffix=True):
        return False
    return True


def _split_inline_numbered_marker_units(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []
    pattern = re.compile(
        r"(?:(?<=^)|(?<=\s)|(?<=[\.\!\?\:\;，,、\)\]\}]))(?P<marker>\(?\d+(?:\.\d+)*[\)\.]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.]|[IVXLCDM]+[\)\.])\s+(?=\S)",
        flags=re.IGNORECASE,
    )
    matches = list(pattern.finditer(normalized))
    if len(matches) <= 1:
        return [normalized]
    units: list[str] = []
    start = 0
    for idx, match in enumerate(matches):
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
        chunk = _normalize_requirement_text(normalized[match.start():next_start])
        if chunk:
            units.append(chunk)
        start = next_start
    if not units:
        return [normalized]
    if start < len(normalized):
        tail = _normalize_requirement_text(normalized[start:])
        if tail and tail not in units:
            units.append(tail)
    return units


def _collapse_repeated_token_tail(value: str) -> str:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return ""
    prefix_match = re.match(
        r"^(?P<prefix>(?:[ㄱ-ㅎ가-하][\.\)]|(?:\(?\d+(?:\.\d+)*[\)\.]?)|(?:[A-Za-z]|[IVXLCDM]+)[\.\)])\s+)(?P<body>.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not prefix_match:
        return normalized
    prefix = _normalize_requirement_text(prefix_match.group("prefix"))
    body = _normalize_requirement_text(prefix_match.group("body"))
    tokens = [token for token in body.split() if token]
    if len(tokens) >= 4 and len(tokens) % 2 == 0:
        half = len(tokens) // 2
        if tokens[:half] == tokens[half:]:
            return _normalize_requirement_text(f"{prefix} {' '.join(tokens[:half])}")
    return normalized


def _is_marker_only_requirement_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    return bool(
        re.fullmatch(r"[ㄱ-ㅎ가-하]\.?", compact)
        or re.fullmatch(r"\(?\d+\)?[\.\)]?", compact)
        or re.fullmatch(r"[A-Za-z][\.\)]?", compact)
        or re.fullmatch(r"[IVXLCDM]+[\.\)]?", compact, flags=re.IGNORECASE)
        or re.fullmatch(r"[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+", compact)
    )


def _looks_like_sentence_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    if len(normalized) >= 40:
        return True
    sentence_markers = [
        "본 사업은",
        "해당 사업은",
        "당사는",
        "해야",
        "필요",
        "제공",
        "지원",
        "구축",
        "적용",
        "준수",
        "가능",
        "수행",
        "제출",
        "이행",
    ]
    return any(marker in normalized for marker in sentence_markers) and len(normalized.split()) >= 4


def _is_title_like_requirement_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    stripped = re.sub(r"^\s*(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+[\.\)]?)\s*", "", normalized, flags=re.IGNORECASE).strip()
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
    if compact.endswith(("사업명", "구축방향", "요청사항", "요구사항", "유의사항", "사업개요", "제안범위", "정보보호요구사항", "기술요건", "보안요건")):
        return True
    return False


def _is_item_name_like_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    if _is_title_like_requirement_text(normalized):
        return True
    if _looks_like_sentence_text(normalized):
        return False
    compact = re.sub(r"\s+", "", normalized)
    return bool(normalized and len(compact) <= 24 and len(normalized.split()) <= 5)


def _has_descendant_bullets(node: dict) -> bool:
    stack = list(node.get("children") or [])
    while stack:
        child = stack.pop()
        text = _normalize_requirement_text(str(child.get("text") or ""))
        if re.match(r"^[\-*•▪■◆▶◦○□◇·ㆍ−–—]+\s*", text):
            return True
        stack.extend(list(child.get("children") or []))
    return False


def _should_skip_requirement_extraction(card: RfpCard) -> tuple[bool, str]:
    title = str(getattr(card, "subject", None) or card.requirement or "")
    group = str(getattr(card, "part", None) or "")
    section = str(getattr(card, "section", None) or "")
    sub_subject = str(getattr(card, "sub_subject", None) or "")
    
    combined_text = " | ".join([title, group, section, sub_subject]).strip()
    
    # 1. 카드 제목(title) 또는 상위 파트(part) 내에서만 검사하는 키워드 (공백 제거 후 비교)
    target_text_for_group = f"{title} | {group}"
    normalized_group_text = re.sub(r"\s+", "", target_text_for_group)
    
    group_specific_keywords = (
        "제안서 작성 및 유의사항",
        "제안서 작성 방안",
        "제안 일반 사항",
        "제안 일반사항",
    )
    for keyword in group_specific_keywords:
        normalized_keyword = re.sub(r"\s+", "", keyword)
        if normalized_keyword in normalized_group_text:
            return True, f"excluded_by_part_keyword_{keyword}"
            
    # 2. 전체 combined_text에서 검사하는 일반 키워드 (공백 제거 후 비교)
    normalized_combined = re.sub(r"\s+", "", combined_text)
    general_keywords = (
        "서식",
        "별첨",
        "입찰 안내",
        "담당자",
        "입찰 일반사항",
        "제안 요청 및 지침",
        "제안 서식 및 별첨",
        "제안 요청서의 효력",
        "유의 사항",
        "제안업체 기본요건",
        "제안서 작성 기준",
        "제안서 작성 방안",
        "제안 일반 사항",
        "제안 일반사항",
        "제출 서류",
        "제안서 평가",
        "선정 방안",
        "기타사항",
        "가격 제안",
        "입찰 제안",
    )
    for keyword in general_keywords:
        normalized_keyword = re.sub(r"\s+", "", keyword)
        if normalized_keyword in normalized_combined:
            return True, f"excluded_by_keyword_{keyword}"
            
    return False, ""



def _normalize_item_name_for_row(item_name: str, requirement: str = "") -> str:
    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)

    candidates: list[str] = []
    for source in (normalized_item, normalized_requirement):
        if not source:
            continue
        heading, detail_lines = _split_body_heading_and_detail_lines(source)
        if heading and heading != source and _is_title_like_requirement_text(heading):
            candidates.append(heading)
            continue
        heading2, inline_details = _split_inline_heading_and_body(source)
        if heading2 and heading2 != source and (
            _is_title_like_requirement_text(heading2) or detail_lines or inline_details
        ):
            candidates.append(heading2)
            continue
        if _is_title_like_requirement_text(source):
            candidates.append(source)

    for candidate in candidates:
        if candidate:
            return candidate

    if normalized_item:
        return normalized_item
    if normalized_requirement:
        return normalized_requirement
    return ""


def _flatten_body_requirement_for_save(
    item_name: str,
    requirement: str,
    detail_requirement: str,
    *,
    build_source: str = "",
    special_rule_applied: bool = False,
) -> tuple[str, str]:
    normalized_source = _normalize_requirement_text(build_source)
    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)
    normalized_detail = _normalize_requirement_text(detail_requirement)
    if normalized_source != "본문":
        return normalized_requirement, normalized_detail
    # 본문은 줄 단위 atomic 출력이 원칙이므로 요구사항을 상세요건에 다시 붙이지 않는다.
    normalized_requirement = normalized_item or normalized_requirement
    if special_rule_applied and normalized_item:
        normalized_requirement = normalized_item
    return normalized_requirement, normalized_detail


def _fallback_item_name_for_marker_only_row(
    item_name: str,
    category_text: str,
    section_title_text: str,
    default_item_name: str,
) -> str:
    normalized_item = _normalize_requirement_text(item_name)
    if not normalized_item:
        return _normalize_requirement_text(default_item_name or category_text or section_title_text)
    if not _is_marker_only_requirement_text(normalized_item):
        return normalized_item
    return _normalize_requirement_text(default_item_name or category_text or section_title_text or normalized_item)


def _describe_build_method(
    build_method: str,
    build_source: str,
    *,
    item_name: str = "",
    requirement: str = "",
    detail_requirement: str = "",
) -> tuple[str, str]:
    normalized_method = _normalize_requirement_text(build_method)
    normalized_source = _normalize_requirement_text(build_source)
    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)
    normalized_detail = _normalize_requirement_text(detail_requirement)
    row_parts = [part for part in (normalized_item, normalized_requirement, normalized_detail) if part]
    distinct_parts: list[str] = []
    for part in row_parts:
        if part not in distinct_parts:
            distinct_parts.append(part)
    row_shape = f"{len(distinct_parts)}단" if distinct_parts else "미상"

    def _snippet(text: str, limit: int = 24) -> str:
        compact = _normalize_requirement_text(text)
        if not compact:
            return ""
        return compact if len(compact) <= limit else f"{compact[:limit]}…"

    row_path_parts = [
        snippet
        for snippet in (_snippet(normalized_item), _snippet(normalized_requirement), _snippet(normalized_detail))
        if snippet
    ]
    row_path = " > ".join(row_path_parts) if row_path_parts else "미상"

    if normalized_source in {"표", "본문", "LLM"}:
        source_label = normalized_source
    elif "LLM" in normalized_method:
        source_label = "LLM"
    elif normalized_source:
        source_label = normalized_source
    else:
        source_label = "룰 기반"

    if normalized_source == "표":
        origin_label = "_extract_rows_from_table_card"
    elif "LLM" in normalized_method:
        origin_label = "_build_card_requirement_rows_via_llm"
    elif "HTML" in normalized_method:
        origin_label = "_extract_hierarchical_body_rows_from_html"
    else:
        origin_label = "_extract_hierarchical_body_rows"
    short_method = normalized_method or ("표" if normalized_source == "표" else "본문")
    table_branch_label = ""
    if normalized_source == "표":
        if "룰 기반(2단 표)" in short_method:
            table_branch_label = "2단표"
        elif "룰 기반(3단 표->2단 표+추가정보)" in short_method:
            table_branch_label = "3단표"
        elif "룰 기반(3단 표-그룹헤더)" in short_method:
            table_branch_label = "3단표"
        elif "룰 기반(4단 표->3단 표+추가정보)" in short_method:
            table_branch_label = "4단표"
        elif "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)" in short_method:
            table_branch_label = "4단표"
        elif "룰 기반(4단 표)" in short_method:
            table_branch_label = "4단표"
        elif "룰 기반(표 fallback)" in short_method:
            table_branch_label = "표fallback"
        elif short_method == "표":
            table_branch_label = "표"
    if "룰 기반(본문 계층 정규화)" in short_method:
        short_method = "본문룰(계층 없음 / atomic)"
    elif "룰 기반(단일 행 본문)" in short_method:
        short_method = "본문룰(계층 없음 / atomic)"
    elif "룰 기반(HTML 계층 정규화)" in short_method:
        short_method = "본문룰(계층 없음 / atomic)"
    elif "룰 기반(표전 본문)" in short_method:
        short_method = "본문룰(계층 없음 / atomic)"
    elif normalized_source != "표" and "룰 기반(표 fallback)" in short_method:
        short_method = "표fallback"
    elif "LLM(표후속공통주석)" in short_method:
        short_method = "LLM"
    elif "LLM+구조정규화" in short_method:
        short_method = "LLM"
    elif short_method == "LLM":
        short_method = "LLM"
    elif normalized_source == "본문":
        short_method = "본문룰(계층 없음 / atomic)"

    applied_rules: list[str] = []
    if normalized_source == "표":
        applied_rules.append("표룰")
        applied_rules.append("표구조인식")
        applied_rules.append("번호열/중복헤더보정")
        applied_rules.append("표칼럼매핑")
        if table_branch_label:
            applied_rules.append(table_branch_label)
    elif "LLM" in normalized_method:
        applied_rules.append("LLM룰")
        applied_rules.append("본문/표재정리")
        applied_rules.append("중복제거정규화")
    else:
        applied_rules.append("본문룰(계층 없음 / atomic)")
        applied_rules.append("atomic평탄화")
        applied_rules.append("불릿/번호 계층 비적용")
        applied_rules.append("정규화")

    if normalized_method.startswith("룰 기반("):
        applied_rules.append(normalized_method)
    if short_method in {"2단표", "3단표", "4단표"}:
        applied_rules.append(short_method)
    if "HTML" in normalized_method:
        applied_rules.append("HTML계층반영")
    if "본문" in normalized_method:
        applied_rules.append("본문줄바꿈/불릿정리")
    if "단일 행" in normalized_method:
        applied_rules.append("단일행분해")
    if "표전" in normalized_method:
        applied_rules.append("표전본문")
    if "fallback" in normalized_method.lower():
        applied_rules.append("fallback")
    if normalized_item:
        applied_rules.append("항목명정규화")
    if normalized_requirement:
        applied_rules.append("요구사항정규화")
    if normalized_detail:
        applied_rules.append("상세요건정리")

    applied_rule = " > ".join(applied_rules)
    return short_method, applied_rule


def _build_source_detail_label(
    build_source: str,
    build_method: str,
    *,
    item_name: str = "",
    requirement: str = "",
    detail_requirement: str = "",
    source_hint: str = "",
) -> str:
    hint = _normalize_requirement_text(source_hint)
    method = _normalize_requirement_text(build_method)
    source = _normalize_requirement_text(build_source)
    item = _normalize_requirement_text(item_name)
    requirement_text = _normalize_requirement_text(requirement)
    detail = _normalize_requirement_text(detail_requirement)

    if source == "표":
        table_label = _format_table_source_detail_label(method, hint)
        if table_label:
            return table_label
    if hint:
        return hint

    if source == "표":
        return "표"

    if "1단 표" in method:
        return "1단표"
    if "룰 기반(2단 표)" in method:
        return "2단표"
    if "룰 기반(3단 표->2단 표+추가정보)" in method:
        return "특수 2단 + 내용"
    if "룰 기반(3단 표-그룹헤더)" in method:
        return "특수 3단 + 내용"
    if "룰 기반(넘버링 표->3단 처리)" in method:
        return "특수 3단 + 내용"
    if "룰 기반(3단 표)" in method:
        return "3단표"
    if "룰 기반(4단 표->3단 표+추가정보)" in method:
        return "특수 4단 + 내용"
    if "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)" in method:
        return "특수 4단 + 내용"
    if "룰 기반(4단 표)" in method:
        return "4단표"
    if "표 fallback" in method:
        return "표 fallback"

    if source == "본문":
        if "LLM" in method:
            return "LLM"
        if "단일 행 본문" in method or "본문 계층 정규화" in method or "HTML 계층 정규화" in method or "표전 본문" in method:
            return "계층 없음 / atomic"
        return "계층 없음 / atomic"

    if "단일 행 본문" in method:
        return "계층 없음 / atomic"
    if "\n" in detail:
        line_count = len([line for line in detail.split("\n") if _normalize_requirement_text(line)])
        if line_count >= 3:
            return "항목명, 요구사항, 상세요건, 상세요건"
        if line_count == 2:
            return "항목명, 요구사항, 상세요건, 상세요건"
    if item and item == requirement_text:
        if detail:
            return "계층 없음 / atomic"
        return "계층 없음 / atomic" if source == "본문" else (source or "룰 기반")
    if requirement_text and requirement_text == detail:
        return "계층 없음 / atomic"
    if item and requirement_text and detail and item != requirement_text and requirement_text != detail:
        if "본문 계층 정규화" in method or "HTML 계층 정규화" in method or "표전 본문" in method:
            return "항목명, 요구사항, 상세요건"
    if "LLM" in method:
        return "LLM"
    return "항목명, 요구사항, 상세요건" if source == "본문" else (source or "룰 기반")


def _format_table_source_detail_label(method: str, source_hint: str = "") -> str:
    hint = _normalize_requirement_text(source_hint)
    if hint.startswith("특수 "):
        return hint
    if hint in {"1단표", "2단표", "3단표", "4단표"}:
        return hint

    normalized_method = _normalize_requirement_text(method)
    if "룰 기반(1단 표" in normalized_method:
        return "1단표"
    if "룰 기반(2단 표)" in normalized_method:
        return "2단표"
    if "룰 기반(3단 표)" in normalized_method:
        return "3단표"
    if "룰 기반(4단 표)" in normalized_method:
        return "4단표"
    if "룰 기반(3단 표->2단 표+추가정보)" in normalized_method:
        return "특수 2단 + 내용"
    if "룰 기반(3단 표-그룹헤더)" in normalized_method:
        return "특수 3단 + 내용"
    if "룰 기반(넘버링 표->3단 처리)" in normalized_method:
        return "특수 3단 + 내용"
    if "룰 기반(4단 표->3단 표+추가정보)" in normalized_method:
        return "특수 4단 + 내용"
    if "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)" in normalized_method:
        return "특수 4단 + 내용"
    if "표 fallback" in normalized_method:
        return "표 fallback"
    return hint


def _table_rule_branch_label(build_method: str) -> str:
    normalized_method = _normalize_requirement_text(build_method)
    if not normalized_method:
        return ""
    if "룰 기반(3단 표->2단 표+추가정보)" in normalized_method:
        return "3단표->2단표+비고/추가정보"
    if "룰 기반(3단 표-그룹헤더)" in normalized_method:
        return "3단표-그룹헤더"
    if "룰 기반(4단 표->3단 표+추가정보)" in normalized_method:
        return "4단표->3단표+추가정보"
    if "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)" in normalized_method:
        return "4단표-번호컬럼제거후3단처리"
    if "룰 기반(4단 표)" in normalized_method:
        return "4단표"
    if "룰 기반(3단 표-박스형 본문)" in normalized_method:
        return "3단표-박스형본문"
    if "룰 기반(넘버링 표->3단 처리)" in normalized_method:
        return "넘버링 표->3단 처리"
    if "룰 기반(2단 표)" in normalized_method:
        return "2단표"
    if "룰 기반(3단 표-단일행 fallback)" in normalized_method:
        return "3단표-단일행fallback"
    if "표 fallback" in normalized_method:
        return "표 fallback"
    if "일반3단표" in normalized_method:
        return "일반3단표"
    return ""


def _format_debug_build_source_label(build_source: str) -> str:
    source = _normalize_requirement_text(build_source)
    if source == "본문":
        return "본문룰(계층 없음 / atomic)"
    return source or ""


def _format_body_source_detail_label(
    *,
    item_name: str = "",
    requirement: str = "",
    detail_requirement: str = "",
    bullet_count: int = 0,
    fragment_level: int | None = None,
) -> str:
    return "계층 없음 / atomic"


def _expand_requirement_rows(rows: list[dict]) -> list[dict]:
    expanded: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item_name = _normalize_requirement_text(str(row.get("item_name") or ""))
        requirement = _normalize_requirement_text(str(row.get("requirement") or ""))
        detail_requirement = _normalize_requirement_text(str(row.get("detail_requirement") or ""))
        result_note = _normalize_requirement_text(str(row.get("result_note") or ""))
        if not detail_requirement:
            continue

        detail_units = _split_atomic_detail_units(detail_requirement) or [detail_requirement]
        for detail_unit in detail_units:
            cleaned = _strip_trailing_orphan_bullet(_normalize_requirement_text(detail_unit))
            build_method_text = _normalize_requirement_text(str(row.get("build_method") or ""))
            if not cleaned or _is_only_page_info(cleaned):
                continue
            if _is_marker_only_requirement_text(cleaned) and "표" not in build_method_text:
                continue
            cleaned = _normalize_requirement_text(cleaned)
            if _is_redundant_same_text_requirement_row(item_name, requirement, cleaned):
                continue
            dedup_detail = _detail_dedup_key_text(cleaned)
            key = (item_name, requirement, dedup_detail, result_note)
            if key in seen:
                continue
            seen.add(key)
            expanded.append(
                {
                    **row,
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": cleaned,
                    "result_note": result_note,
                }
            )
    return expanded


def _strip_leading_numbering(value: str) -> str:
    compact = _normalize_requirement_text(value)
    if not compact:
        return ""
    stripped = re.sub(
        r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
        "",
        compact,
        flags=re.IGNORECASE,
    )
    return stripped.strip() or compact


def _has_leading_numbering(value: str) -> bool:
    compact = _normalize_requirement_text(value)
    if not compact:
        return False
    return bool(
        re.match(
            r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
            compact,
            flags=re.IGNORECASE,
        )
    )


def _drop_numbering_column_from_matrix(matrix: list[list[str]]) -> tuple[list[list[str]], int | None]:
    if not matrix:
        return matrix, None
    max_cols = max(len(row) for row in matrix)
    if max_cols not in {4, 5}:
        return matrix, None

    best_col: int | None = None
    best_score = -1.0
    for col_idx in range(max_cols):
        values = [str(row[col_idx]).strip() for row in matrix if col_idx < len(row)]
        non_empty = [value for value in values if value]
        if len(non_empty) < max(2, len(values) // 2):
            continue
        numbering_like = sum(
            1
            for value in non_empty
            if re.fullmatch(r"\(?\d+(?:\.\d+)*[\)\.]?", value) is not None
            or (_has_leading_numbering(value) and len(_strip_leading_numbering(value)) <= 2)
        )
        
        # 헤더 셀이 명백히 순번을 나타내는 키워드인 경우 가중치를 주어 데이터 행이 적은 소형 표에서도 드롭이 활성화되도록 유도
        header_val = values[0] if values else ""
        header_bonus = 0.0
        if re.sub(r"\s+", "", header_val).lower() in {"순번", "번호", "no", "no.", "num", "number", "id"}:
            header_bonus = 0.3
            
        score = (numbering_like / max(len(non_empty), 1)) + header_bonus
        if score >= 0.7 and score > best_score:
            best_score = score
            best_col = col_idx

    if best_col is None:
        return matrix, None

    reduced: list[list[str]] = []
    for row in matrix:
        reduced.append([cell for idx, cell in enumerate(row) if idx != best_col])
    return reduced, best_col


def _split_atomic_detail_units(value: str) -> list[str]:
    raw_value = str(value or "")
    raw_lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
    if len(raw_lines) >= 2:
        first_line = _normalize_requirement_text(raw_lines[0])
        second_line = _normalize_requirement_text(raw_lines[1])
        if (
            re.match(r"^[\-*·ㆍ−–—]+\s*.+$", first_line)
            and re.match(r"^M\s*-\s*\d+(?:\s*(?:개월|주|일|개월차|주차|월|년|단계|시점|전|후))?.+$", second_line, flags=re.IGNORECASE)
        ):
            return [_normalize_requirement_text(f"{first_line} {second_line}")]

    normalized = _normalize_middle_dot_connectors(value)
    if not normalized:
        return []
    if _has_quoted_bullet_marker(normalized):
        return [normalized]
    if _looks_like_asterisk_quantity_expression(normalized):
        return [normalized]
    repeated_bullet_units = _split_repeated_bullet_chain(normalized)
    if len(repeated_bullet_units) > 1:
        return repeated_bullet_units
    standalone_bullet_units = _split_inline_standalone_bullet_units(normalized)
    if len(standalone_bullet_units) > 1:
        return standalone_bullet_units

    def _looks_like_hyphenated_noun_phrase(text: str) -> bool:
        compact = _normalize_requirement_text(text)
        if not compact or "\n" in compact:
            return False
        if _looks_like_sentence_text(compact):
            return False
        if any(token in compact for token in ["해야", "필요", "분리", "제시", "구성", "협의"]):
            return False
        parts = [part.strip() for part in re.split(r"\s+[-–—]\s+", compact) if part.strip()]
        if len(parts) != 2:
            return False
        if any(re.search(r"[-–—]", part) for part in parts):
            return False
        return all(
            re.fullmatch(r"[가-힣A-Za-z0-9/()]{1,20}(?:\s+[가-힣A-Za-z0-9/()]{1,20}){0,2}", part)
            for part in parts
        )

    # 단일 대시로 연결된 짧은 명사구만 예외로 유지한다.
    # 그 외의 문장 중간 대시는 계속 분리 대상으로 본다.
    if _looks_like_hyphenated_noun_phrase(normalized):
        return [normalized]
    if _looks_like_connected_dash_phrase(normalized):
        return [normalized]

    numbered_marker_units = _split_inline_numbered_marker_units(normalized)
    if len(numbered_marker_units) > 1:
        return numbered_marker_units
    repeated_bullet_units = _split_repeated_bullet_chain(normalized)
    if len(repeated_bullet_units) > 1:
        return repeated_bullet_units
    standalone_bullet_units = _split_inline_standalone_bullet_units(normalized)
    if len(standalone_bullet_units) > 1:
        return standalone_bullet_units
    if _has_inline_middle_dot_connector(normalized) and not re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return [normalized]
    if _is_reference_like_body_line(normalized):
        return [normalized]
    if _has_inline_numeric_bullet_not_at_start(normalized):
        return [normalized]

    # OCR 결과가 "제 3 자", "제 3 자의"처럼 잘못 띄어지는 경우가 많다.
    # 이 상태로 두면 "3"을 목록 번호 마커로 오인해서 상세요건이 비정상 분리된다.
    normalized = re.sub(r"제\s+(\d+)\s+자", r"제\1자", normalized)

    # 문장형으로 보이더라도 반복 불릿이 섞여 있으면 먼저 분리한다.
    # 예: "① 검색 기능 • ... • ..." 같은 표/본문 혼합 케이스.
    repeated_bullet_units = _split_repeated_bullet_chain(normalized)
    if len(repeated_bullet_units) > 1:
        return repeated_bullet_units
    standalone_bullet_units = _split_inline_standalone_bullet_units(normalized)
    if len(standalone_bullet_units) > 1:
        return standalone_bullet_units

    if _looks_like_sentence_text(normalized) and not re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return [normalized]

    korean_chain_split = _split_inline_korean_letter_requirements([normalized])
    if len(korean_chain_split) > 1:
        return [unit for unit in korean_chain_split if unit]

    # 단일 마커 줄은 마커와 본문을 분리하지 말고 한 단위로 유지한다.
    # 예: "ㄴ. 설명"을 ["ㄴ.", "설명"]으로 쪼개지 않게 한다.
    if re.match(r"^\s*(?:[가-하ㄱ-ㅎ]|[A-Za-z]|[IVXLCDM]+)[\.\)]\s+.+$", normalized, flags=re.IGNORECASE):
        marker_hits = len(
            re.findall(
                r"(?:^|\s)(?:[가-하ㄱ-ㅎ]|[A-Za-z]|[IVXLCDM]+)[\.\)]\s+",
                normalized,
                flags=re.IGNORECASE,
            )
        )
        if marker_hits <= 1:
            return [normalized]

    def merge_numbered_middle_dot_chain(units: list[str]) -> list[str]:
        if len(units) <= 1:
            return units
        merged: list[str] = []
        idx = 0
        numbered_pattern = re.compile(
            r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
            flags=re.IGNORECASE,
        )
        middle_dot_pattern = re.compile(r"^\s*[·ㆍ]\s*(.+?)\s*$")
        while idx < len(units):
            current = _normalize_requirement_text(units[idx])
            if not current:
                idx += 1
                continue
            if numbered_pattern.match(current):
                chain = [current]
                look_ahead = idx + 1
                dot_count = 0
                while look_ahead < len(units):
                    candidate = _normalize_requirement_text(units[look_ahead])
                    dot_match = middle_dot_pattern.match(candidate)
                    if not dot_match:
                        break
                    dot_count += 1
                    chain.append(candidate)
                    look_ahead += 1
                if dot_count >= 2:
                    merged.append(" ".join(chain).strip())
                    idx = look_ahead
                    continue
            merged.append(current)
            idx += 1
        return merged

    raw_lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    marker_prefix_pattern = (
        r"(?:[①-⑳㉠-㉾❶-❿]+|[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|"
        r"\(?\d+(?:\.\d+)*[\)\.\-]|"
        r"(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|"
        r"[IVXLCDM]+[\)\.\-])"
    )
    inline_marker_prefix_pattern = (
        r"(?:[①-⑳㉠-㉾❶-❿]+|"
        r"(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|"
        r"[IVXLCDM]+[\)\.\-]|"
        r"[▪■◆▶○□◇⦁−–—\-\*]+)"
    )
    inline_split_pattern = re.compile(
        r"(?:(?<=^)|(?<=\s)|(?<=[\.\!\?\:\;，,、\)\]\}]))(?P<marker>" + inline_marker_prefix_pattern + r")\s*",
        flags=re.IGNORECASE,
    )

    def split_inline_markers(line: str) -> list[str]:
        text = _normalize_requirement_text(line)
        if not text:
            return []
        if _looks_like_leading_bullet_connector_phrase(text) or _looks_like_dash_connected_term_chain(text):
            return [text]
        matches = list(inline_split_pattern.finditer(text))
        if len(matches) <= 1:
            return [text]
        pieces: list[str] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            chunk = _normalize_requirement_text(text[start:end])
            if chunk:
                pieces.append(chunk)
        return pieces or [text]

    expanded_lines: list[str] = []
    for line in raw_lines:
        expanded_lines.extend(split_inline_markers(line))
    raw_lines = expanded_lines or raw_lines

    has_explicit_marker_lines = any(
        re.match(rf"^{marker_prefix_pattern}\s*", line, flags=re.IGNORECASE)
        for line in raw_lines
    )
    if has_explicit_marker_lines:
        units: list[str] = []
        preamble_lines: list[str] = []
        current_unit = ""
        for line in raw_lines:
            marker_only = re.match(
                rf"^(?P<prefix>{marker_prefix_pattern})\s*$",
                line,
                flags=re.IGNORECASE,
            )
            marker_start = re.match(
                rf"^(?P<prefix>{marker_prefix_pattern})\s*(?P<body>.+)$",
                line,
                flags=re.IGNORECASE,
            )
            if marker_only:
                if current_unit:
                    units.append(current_unit.strip())
                elif preamble_lines:
                    units.append(" ".join(preamble_lines).strip())
                    preamble_lines = []
                current_unit = marker_only.group("prefix").strip()
                continue
            if marker_start:
                if current_unit:
                    units.append(current_unit.strip())
                elif preamble_lines:
                    units.append(" ".join(preamble_lines).strip())
                    preamble_lines = []
                current_unit = line
                continue
            if current_unit:
                current_unit = f"{current_unit} {line}".strip()
            else:
                preamble_lines.append(line)
        if current_unit:
            units.append(current_unit.strip())
        elif preamble_lines:
            units.append(" ".join(preamble_lines).strip())
        return [unit for unit in merge_numbered_middle_dot_chain(units) if unit]

    units: list[str] = []
    pending_prefix = ""
    for line in raw_lines:
        if not line:
            continue
        marker_only = re.match(
            rf"^(?P<prefix>{marker_prefix_pattern})\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if marker_only:
            pending_prefix = marker_only.group("prefix").strip()
            continue
        if pending_prefix:
            units.append(f"{pending_prefix} {line}".strip())
            pending_prefix = ""
            continue
        units.append(line)

    if pending_prefix:
        units.append(pending_prefix)

    if len(units) > 1:
        return merge_numbered_middle_dot_chain(units)

    single = units[0] if units else normalized
    if re.match(rf"^\s*{marker_prefix_pattern}\s*", single, flags=re.IGNORECASE):
        return [single.strip()]
    sentence_parts = [
        part.strip()
        for part in re.split(r"(?<![가-힣A-Za-z0-9]\.)(?<=[\.\!\?])\s+|(?<=함)\s+(?=[\-•▪■◆▶◦○□◇])", single)
        if part.strip()
    ]
    if len(sentence_parts) > 1:
        return sentence_parts
    return [single.strip()] if single.strip() else []


def _has_explicit_bullet_marker_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    bullet_pattern = re.compile(
        r"(?:(?<=^)|(?<=\s)|(?<=\n))(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        flags=re.IGNORECASE,
    )
    return bool(bullet_pattern.search(normalized))


def _split_body_detail_units_max_two_sentences(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []
    if _is_reference_like_body_line(normalized):
        return [normalized]
    if _has_inline_numeric_bullet_not_at_start(normalized):
        return [normalized]

    initial_units = _split_atomic_detail_units(normalized) or [normalized]
    final_units: list[str] = []
    for unit in initial_units:
        compact = _normalize_requirement_text(unit)
        if not compact:
            continue
        if re.match(r"^(?:[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*", compact, flags=re.IGNORECASE):
            final_units.append(compact)
            continue
        sentence_parts = [
            part.strip()
            for part in re.split(r"(?<![가-힣A-Za-z0-9]\.)(?<=[\.\!\?])\s+", compact)
            if part.strip()
        ]
        if len(sentence_parts) <= 2:
            final_units.append(compact)
            continue
        for idx in range(0, len(sentence_parts), 2):
            chunk = " ".join(sentence_parts[idx: idx + 2]).strip()
            if chunk:
                final_units.append(chunk)
    return final_units or ([normalized] if normalized else [])


def _split_detail_units_one_sentence(value: str) -> list[str]:
    normalized = _normalize_middle_dot_connectors(value)
    if not normalized:
        return []
    if re.match(
        r"^\s*[\-*·ㆍ−–—]+\s*.+\bM\s*-\s*\d+(?:\s*(?:개월|주|일|개월차|주차|월|년|단계|시점|전|후))?.*$",
        normalized,
        flags=re.IGNORECASE,
    ):
        return [normalized]
    numbered_marker_units = _split_inline_numbered_marker_units(normalized)
    if len(numbered_marker_units) > 1:
        return numbered_marker_units
    repeated_bullet_units = _split_repeated_bullet_chain(normalized)
    if len(repeated_bullet_units) > 1:
        return repeated_bullet_units
    standalone_bullet_units = _split_inline_standalone_bullet_units(normalized)
    if len(standalone_bullet_units) > 1:
        return standalone_bullet_units
    if _has_inline_middle_dot_connector(normalized) and not re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return [normalized]
    if _is_reference_like_body_line(normalized):
        return [normalized]
    if _has_inline_numeric_bullet_not_at_start(normalized):
        return [normalized]

    initial_units = _split_atomic_detail_units(normalized) or [normalized]
    final_units: list[str] = []
    marker_pattern = re.compile(
        r"^(?P<prefix>(?:[①-⑳㉠-㉾❶-❿]+|[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-]))\s*(?P<body>.+)$",
        flags=re.IGNORECASE,
    )
    for unit in initial_units:
        compact = _normalize_requirement_text(unit)
        if not compact:
            continue
        marker_match = marker_pattern.match(compact)
        prefix = ""
        body = compact
        if marker_match:
            prefix = _normalize_requirement_text(str(marker_match.group("prefix") or ""))
            body = _normalize_requirement_text(str(marker_match.group("body") or ""))
        sentence_parts = [
            part.strip()
            for part in re.split(r"(?<=[\.\!\?])\s+(?=[A-Z0-9가-힣])", body)
            if part.strip()
        ]
        if not sentence_parts:
            sentence_parts = [body] if body else []
        if prefix:
            final_units.extend([f"{prefix} {part}".strip() for part in sentence_parts if part.strip()])
        else:
            final_units.extend([part for part in sentence_parts if part.strip()])
    return final_units or ([normalized] if normalized else [])


def _split_table_cell_lines(value: str) -> list[str]:
    raw_value = str(value or "")
    if not raw_value:
        return []
    lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
    if not lines:
        compact = _normalize_requirement_text(raw_value)
        return [compact] if compact else []
    split_lines: list[str] = []
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if not normalized:
            continue
        split_lines.extend(_split_atomic_detail_units(normalized) or [normalized])
    return split_lines


def _split_three_col_detail_units(value: str) -> list[str]:
    normalized = _normalize_middle_dot_connectors(value)
    if not normalized:
        return []
    numbered_marker_units = _split_inline_numbered_marker_units(normalized)
    if len(numbered_marker_units) > 1:
        return numbered_marker_units
    repeated_bullet_units = _split_repeated_bullet_chain(normalized)
    if len(repeated_bullet_units) > 1:
        return repeated_bullet_units
    standalone_bullet_units = _split_inline_standalone_bullet_units(normalized)
    if len(standalone_bullet_units) > 1:
        return standalone_bullet_units
    if _has_inline_middle_dot_connector(normalized) and not re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return [normalized]
    if _is_reference_like_body_line(normalized):
        return [normalized]
    if _has_inline_numeric_bullet_not_at_start(normalized):
        return [normalized]

    if _has_explicit_bullet_marker_text(normalized):
        return _split_atomic_detail_units(normalized) or [normalized]

    line_parts = [
        _normalize_requirement_text(part)
        for part in normalized.split("\n")
        if _normalize_requirement_text(part)
    ]
    if len(line_parts) > 1:
        return line_parts

    compact = line_parts[0] if line_parts else normalized
    sentence_parts = [
        part.strip()
        for part in re.split(r"(?<=[\.\!\?])\s+(?=[A-Z0-9가-힣])", compact)
        if part.strip()
    ]
    return sentence_parts or ([compact] if compact else [])


def _split_single_column_table_lines(matrix: list[list[str]]) -> list[str]:
    lines: list[str] = []
    for row in matrix:
        for cell in row:
            cell_text = _cell_text_preserve_breaks(cell)
            for raw_line in str(cell_text or "").splitlines():
                normalized = _normalize_requirement_text(raw_line)
                if normalized:
                    lines.append(normalized)
    return lines


def _strip_single_column_table_marker(value: str) -> str:
    compact = _normalize_requirement_text(value)
    if not compact:
        return ""
    stripped = _strip_leading_numbering(compact)
    if stripped != compact:
        return stripped
    stripped = re.sub(r"^\s*[-•·▪■◆▶◦○□◇⦁\*]+\s+", "", compact).strip()
    return stripped or compact


def _extract_single_column_table_rows(
    card: RfpCard,
    matrix: list[list[str]],
    card_title: str,
    preferred_item_context: str,
    has_header_row: bool,
) -> list[dict]:
    item_name = _normalize_requirement_text(preferred_item_context)
    if not item_name:
        item_name = str(getattr(card, "sub_subject", None) or getattr(card, "subject", None) or card.requirement or "").strip()
    if not item_name:
        item_name = card_title

    lines = _split_single_column_table_lines(matrix)
    if not lines:
        return []

    if has_header_row:
        first_line = lines[0]
        if _is_header_like_field(first_line) or _normalize_requirement_text(first_line) == _normalize_requirement_text(card_title):
            lines = lines[1:]

    rows: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    current_requirement = card_title
    emitted_requirement = ""

    def _emit_row(requirement_text: str, detail_text: str, build_method: str) -> None:
        requirement = _normalize_requirement_text(requirement_text) or card_title
        detail = _normalize_requirement_text(detail_text)
        if not detail:
            return
        detail_units = _split_atomic_detail_units(detail) or [detail]
        for detail_unit in detail_units:
            detail_unit = _normalize_requirement_text(detail_unit)
            if not detail_unit:
                continue
            key = ("", item_name, requirement, detail_unit)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": detail_unit,
                    "result_note": "",
                    "id_title_hint": "",
                    "build_method": build_method,
                }
            )

    for line in lines:
        bullet_level = _detect_bullet_level(line)
        line_body = _strip_single_column_table_marker(line)
        if bullet_level == 2 and line_body:
            current_requirement = line_body
            emitted_requirement = current_requirement
            _emit_row(current_requirement, line_body, "룰 기반(1단 표-레벨2)")
            continue

        requirement = emitted_requirement or current_requirement or card_title
        if bullet_level == 1 and not emitted_requirement:
            requirement = card_title
        _emit_row(requirement, line, "룰 기반(1단 표-불릿/줄)")

    return rows


def _plain_text_from_html_excerpt(html_excerpt: str) -> str:
    if not html_excerpt:
        return ""
    soup = BeautifulSoup(html_excerpt, "html.parser")
    return _normalize_ocr_bullet_markers(soup.get_text("\n", strip=False))


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


def _is_faux_table_label_text(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    if _has_leading_numbering(normalized):
        return False
    if re.match(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+\s*", normalized):
        return False
    if _is_heading_like_text(normalized):
        return False
    if len(normalized) <= 16:
        return True
    return len(normalized) <= 28 and len(normalized.split()) <= 4


def _looks_like_faux_table_row_content(lines: list[str]) -> bool:
    normalized_lines = [_normalize_requirement_text(line) for line in lines if _normalize_requirement_text(line)]
    if not normalized_lines:
        return False
    if len(normalized_lines) >= 2:
        return True
    line = normalized_lines[0]
    if re.match(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+\s*", line):
        return True
    if _has_leading_numbering(line):
        return True
    return len(line) >= 36


def _leading_body_context_from_table_html(html_excerpt: str, fallback_title: str = "") -> str:
    blocks = _body_text_blocks(html_excerpt)
    if not blocks:
        return _normalize_requirement_text(fallback_title)

    title_norm = _normalize_requirement_text(fallback_title)
    leading_texts: list[str] = []
    for block in blocks:
        if block.get("tag") == "table":
            break
        text = _normalize_requirement_text(str(block.get("text") or ""))
        if not text:
            continue
        if title_norm and text == title_norm:
            continue
        leading_texts.append(text)

    if not leading_texts:
        return title_norm
    return _normalize_requirement_text("\n".join(leading_texts))


def _is_promotable_leading_table_heading(text: str, card_title: str = "") -> bool:
    normalized = _normalize_requirement_text(text)
    normalized_title = _normalize_requirement_text(card_title)
    if not normalized or normalized == normalized_title:
        return False
    if len(normalized) > 40:
        return False
    if re.match(r"^[○●◦•□▪■◆▶◇※]+\s*", normalized):
        return True
    return _is_heading_like_text(normalized)


def _normalize_two_col_table_item_name(context_text: str, fallback_title: str = "") -> str:
    def _strip_leading_numbering(text: str, *, preserve_korean_prefix: bool = False) -> str:
        normalized = _normalize_requirement_text(text)
        if not normalized:
            return ""
        if preserve_korean_prefix and re.match(
            r"^\s*(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]\s*",
            normalized,
            flags=re.IGNORECASE,
        ):
            return normalized
        return re.sub(
            r"^\s*(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*[\.\)]?|[IVXLCDM]+[\.\)]?|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]?)\s*",
            "",
            normalized,
            flags=re.IGNORECASE,
        ).strip()

    def _preserve_security_domain(source_text: str, resolved_text: str) -> str:
        normalized_source = _normalize_requirement_text(source_text)
        normalized_resolved = _normalize_requirement_text(resolved_text)
        if not normalized_source or not normalized_resolved:
            return normalized_resolved
        if any(token in normalized_source for token in ["정보보호", "보안", "컴플라이언스"]):
            if not any(token in normalized_resolved for token in ["정보보호", "보안", "컴플라이언스"]):
                if any(token in normalized_resolved for token in ["활용", "연계", "표준", "접근", "인증", "계정"]):
                    return f"{normalized_resolved} 정보보호"
        return normalized_resolved

    def _finalize_item_name(text: str, *, preserve_korean_prefix: bool = False) -> str:
        normalized = _strip_leading_numbering(text, preserve_korean_prefix=preserve_korean_prefix)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"^[\)\]\}]+", "", normalized).strip()
        normalized = re.sub(r"\s+관련$", "", normalized).strip()
        normalized = re.sub(r"\s+규격\s*및(?:\s*(?:요구사항|요건|내용|세부\s*내용))?$", "", normalized).strip()
        normalized = re.sub(r"\s+(?:요청\s*사항|요구사항|요건|통제\s*요구사항)$", "", normalized).strip()
        return normalized

    def _prefer_source_when_resolution_too_generic(resolved_text: str, source_text: str) -> str:
        normalized_resolved = _normalize_requirement_text(resolved_text)
        normalized_source = _strip_leading_numbering(source_text, preserve_korean_prefix=preserve_korean_prefix)
        if not normalized_resolved:
            return normalized_source
        if not normalized_source:
            return normalized_resolved
        if _looks_like_broad_requirement_group_label(normalized_resolved, normalized_source, normalized_source):
            if len(normalized_source) > len(normalized_resolved):
                return normalized_source
        return normalized_resolved

    preserve_korean_prefix = bool(
        re.match(r"^\s*(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]\s*", _normalize_requirement_text(context_text), flags=re.IGNORECASE)
        or re.match(r"^\s*(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]\s*", _normalize_requirement_text(fallback_title), flags=re.IGNORECASE)
    )
    context = _strip_leading_numbering(context_text, preserve_korean_prefix=preserve_korean_prefix)
    title = _strip_leading_numbering(fallback_title, preserve_korean_prefix=preserve_korean_prefix)
    candidates = []
    if _is_item_name_like_text(context):
        candidates.append(context)
    if _is_item_name_like_text(title):
        candidates.append(title)
    patterns: list[tuple[str, callable]] = [
        (
            r"(.+?)\s+활용관련\s+정보보호\s+요구사항$",
            lambda m: f"{_normalize_requirement_text(m.group(1))} 활용",
        ),
        (
            r"(.+?)\s+기능그룹별\s+정보보호\s+요구사항$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
        (
            r"(.+?)\s+세부\s+정보보호\s+요건$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
        (
            r"(.+?)\s+관련\s+정보보호\s+요구사항$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
        (
            r"(.+?)\s+정보보호\s+요구사항$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
        (
            r"(.+?)\s+보안\s+요건$",
            lambda m: f"{_normalize_requirement_text(m.group(1))} 보안",
        ),
        (
            r"(.+?)\s+요청\s*사항$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
    ]
    for text in candidates:
        if not text:
            continue
        for pattern, resolver in patterns:
            match = re.search(pattern, text)
            if match:
                resolved = _finalize_item_name(
                    resolver(match),
                    preserve_korean_prefix=preserve_korean_prefix,
                )
                resolved = _preserve_security_domain(text, resolved)
                if resolved:
                    return _prefer_source_when_resolution_too_generic(resolved, text)
    fallback_source = title or context
    fallback_resolved = _finalize_item_name(fallback_source, preserve_korean_prefix=preserve_korean_prefix)
    fallback_resolved = _prefer_source_when_resolution_too_generic(fallback_resolved, fallback_source)
    if not _is_item_name_like_text(context) and _is_item_name_like_text(title):
        return _preserve_security_domain(title, fallback_resolved)
    if _is_item_name_like_text(context):
        context_resolved = _finalize_item_name(context, preserve_korean_prefix=preserve_korean_prefix)
        context_resolved = _prefer_source_when_resolution_too_generic(context_resolved, context)
        return _preserve_security_domain(context, context_resolved)
    return _preserve_security_domain(fallback_source, fallback_resolved)


def _normalize_three_col_table_item_name(
    raw_item_name: str,
    context_text: str,
    fallback_title: str = "",
) -> str:
    normalized_item = _normalize_requirement_text(raw_item_name)
    compact = re.sub(r"[\s\-\_/,:;()\[\]{}]+", "", normalized_item).lower()
    generic_tokens = {
        "requirement",
        "requirements",
        "item",
        "itemname",
        "category",
        "group",
        "section",
        "subject",
        "title",
        "구분",
        "항목",
        "항목명",
        "요구사항",
        "상세",
        "상세요건",
        "상세요건",
        "내용",
    }
    if normalized_item and not _is_header_like_field(normalized_item) and compact not in generic_tokens:
        return normalized_item
    contextual_item_name = _normalize_two_col_table_item_name(context_text, fallback_title)
    return contextual_item_name or normalized_item


def _item_name_similarity_key(value: str) -> str:
    normalized = _normalize_requirement_text(value)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"(?:정보보호|보안|컴플라이언스)$", "", normalized)
    return normalized


def _unify_similar_table_item_names(rows: list[dict]) -> None:
    if not rows:
        return

    variants: dict[str, list[str]] = {}
    for row in rows:
        item_name = _normalize_requirement_text(str(row.get("item_name") or ""))
        if not item_name:
            continue
        key = _item_name_similarity_key(item_name)
        if not key:
            continue
        variants.setdefault(key, [])
        if item_name not in variants[key]:
            variants[key].append(item_name)

    canonical_by_key: dict[str, str] = {}
    for key, names in variants.items():
        if not names:
            continue
        canonical = sorted(
            names,
            key=lambda name: (
                0 if any(token in name for token in ["정보보호", "보안", "컴플라이언스"]) else 1,
                -len(name),
                name,
            ),
        )[0]
        canonical_by_key[key] = canonical

    if not canonical_by_key:
        return

    for row in rows:
        item_name = _normalize_requirement_text(str(row.get("item_name") or ""))
        if not item_name:
            continue
        key = _item_name_similarity_key(item_name)
        canonical = canonical_by_key.get(key)
        if canonical:
            row["item_name"] = canonical


def _is_auxiliary_third_column_header(header_row: list[str]) -> bool:
    if len(header_row) < 3:
        return False
    third = _normalize_requirement_text(str(header_row[2] or ""))
    if not third:
        return False
    compact_third = re.sub(r"\s+", "", third).lower()
    patterns = [
        "비고",
        "비고사항",
        "참고사항",
        "관련 법규",
        "관련법규",
        "가이드라인",
        "내규",
        "참고",
        "참조",
        "근거",
        "법령",
        "규정",
        "수량",
        "수량(ea)",
        "수량(대)",
        "수량(개)",
        "수량(식)",
        "수량(set)",
        "ea",
        "단위",
        "수량/단위",
        "단위/수량",
        "수량 / 단위",
    ]
    compact_patterns = [re.sub(r"\s+", "", pattern).lower() for pattern in patterns]
    return any(pattern in compact_third for pattern in compact_patterns)


def _is_numeric_like_auxiliary_value(value: str) -> bool:
    compact = _normalize_requirement_text(value).strip().lower()
    if not compact:
        return False
    cleaned = re.sub(r"(ea|대|개|식|set|세트|box|박스|명|권|대수|수량|ea\b|set\b)$", "", compact).strip()
    if re.search(r"[a-z가-힣]", cleaned):
        return False
    return bool(re.fullmatch(r"[\d,\.\-/()+%×xX\s]+", cleaned))


def _is_numeric_auxiliary_third_column(matrix: list[list[str]]) -> bool:
    if not matrix:
        return False
    max_cols = max(len(row) for row in matrix)
    if max_cols != 3:
        return False
    values = [
        _normalize_requirement_text(str(row[2] if len(row) > 2 else ""))
        for row in matrix[1:]
        if len(row) > 2
    ]
    non_empty = [value for value in values if value]
    if not non_empty:
        return False
    numeric_like = sum(1 for value in non_empty if _is_numeric_like_auxiliary_value(value))
    return numeric_like / max(len(non_empty), 1) >= 0.8


def _is_auxiliary_fourth_column_header(header_row: list[str]) -> bool:
    if len(header_row) < 4:
        return False
    fourth = _normalize_requirement_text(str(header_row[3] or ""))
    if not fourth:
        return False
    compact_fourth = re.sub(r"\s+", "", fourth).lower()
    patterns = [
        "비고",
        "비고사항",
        "참고사항",
        "참고",
        "추가정보",
        "비고란",
        "수량",
        "수량(ea)",
        "수량(대)",
        "수량(개)",
        "수량(식)",
        "수량(set)",
        "ea",
        "단위",
        "수량/단위",
        "단위/수량",
        "수량 / 단위",
    ]
    compact_patterns = [re.sub(r"\s+", "", pattern).lower() for pattern in patterns]
    return any(pattern in compact_fourth for pattern in compact_patterns)


def _is_grouped_three_level_header(header_row: list[str]) -> bool:
    if len(header_row) != 3:
        return False
    normalized = [_normalize_requirement_text(str(cell or "")) for cell in header_row]
    first, second, third = normalized
    if not third:
        return False
    if third in {"요구사항 상세", "상세", "상세내용", "요구사항", "요구 사항 상세"}:
        return first == second and first in {"구분", "분류", "항목"}
    return False


def _is_single_row_header_continuation_table(matrix: list[list[str]], has_header_row: bool) -> bool:
    if not has_header_row or len(matrix) != 1:
        return False
    row = list(matrix[0]) if matrix else []
    if len(row) not in {2, 3, 4}:
        return False
    normalized = [_normalize_requirement_text(str(cell or "")) for cell in row]
    non_empty = [cell for cell in normalized if cell]
    if len(non_empty) != 1:
        return False
    detail = non_empty[0]
    if len(detail) < 20:
        return False
    return any(token in detail for token in ("가.", "나.", "다.", "라.", "마.", "바.", "사.", "-", "•", "▪", "※"))


def _is_box_style_three_column_table(matrix: list[list[str]]) -> bool:
    if not matrix:
        return False
    if max(len(row) for row in matrix) != 3:
        return False
    has_content = False
    for row in matrix:
        padded = list(row) + [""] * max(0, 3 - len(row))
        first = _normalize_requirement_text(str(padded[0] or ""))
        second = _normalize_requirement_text(str(padded[1] or ""))
        third = _normalize_requirement_text(str(padded[2] or ""))
        if not third:
            return False
        if first or second:
            return False
        has_content = True
    return has_content


def _is_noisy_table_item_name(value: str) -> bool:
    raw_value = re.sub(r"\s+", " ", str(value or "")).strip()
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return True
    if _is_header_like_field(normalized):
        return True
    return bool(re.match(r"^[\)\]\}\-–—]+", raw_value))


def _infer_two_col_table_item_name_via_llm(client, card: RfpCard, section_context: dict, model_name: str) -> tuple[str, dict]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if "<table" not in html_excerpt.lower():
        return "", {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    if not tables:
        return "", {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    target_table = None
    target_matrix: list[list[str]] = []
    for idx, table in enumerate(tables, start=1):
        matrix = _table_visual_matrix(table, preserve_breaks=True)
        if not matrix:
            continue
        max_cols_before_drop = max(len(row) for row in matrix)
        if max_cols_before_drop != 4:
            matrix, _ = _drop_numbering_column_from_matrix(matrix)
        max_cols = max(len(row) for row in matrix)
        header_row = [str(cell or "").strip() for cell in matrix[0]] if matrix else []
        if max_cols == 2 or (max_cols == 3 and _is_auxiliary_third_column_header(header_row)):
            target_table = table
            target_matrix = matrix
            break

    if target_table is None or not target_matrix:
        return "", {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    leading_context = _leading_body_context_from_table_html(html_excerpt, card_title)
    top_table_context, _ = _top_table_context_from_matrix(target_matrix, card_title)
    rule_based_hint = _normalize_two_col_table_item_name(
        str(leading_context or top_table_context or card.requirement or getattr(card, "subject", None) or ""),
        card_title,
    )
    first_column_preview: list[str] = []
    if target_matrix:
        row_start_idx = 1 if len(target_matrix) > 1 else 0
        for row in target_matrix[row_start_idx: row_start_idx + 6]:
            first_value = _normalize_requirement_text(str((row[0] if row else "") or ""))
            if first_value and first_value not in first_column_preview:
                first_column_preview.append(first_value)
    payload = {
        "section_context": {
            "section_title": str(section_context.get("section_title") or ""),
            "default_item_name": str(section_context.get("default_item_name") or ""),
        },
        "card": {
            "card_no": getattr(card, "card_no", None) or card.card_id,
            "subject": card_title,
            "requirement": str(card.requirement or ""),
            "leading_context": leading_context,
            "top_table_context": top_table_context,
            "rule_based_hint": rule_based_hint,
            "table_header": target_matrix[0] if target_matrix else [],
            "first_column_preview": first_column_preview,
            "table_rows_preview": target_matrix[:6],
            "html_excerpt": str(target_table)[:8000],
        },
    }

    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You infer only the level-1 item_name for one 2-column RFP table. "
                    "Return JSON object only with key: item_name. "
                    "Choose the most specific accurate category noun phrase that sits exactly one level above the table's first-column groups. "
                    "Do not return a full sentence. Do not return an explanation. "
                    "Prefer a compact domain/category such as '정보처리시스템', '개인정보처리시스템', '공통', '서비스 설계(UX)', '그래픽 디자인(UI)'. "
                    "Choose the best item_name by jointly considering the sentence before the table, the table header, and the section/card title. "
                    "If the sentence before the table gives a narrower parent category than the section title, prefer that narrower category. "
                    "Use the card title, leading_context, top_table_context, first_column_preview, and html_excerpt together. "
                    "Do not just repeat section_context.default_item_name unless there is no narrower table-specific category in the card title or surrounding context. "
                    "Different tables in the same section should produce different item_name values when their surrounding context points to different domains. "
                    "The first-column values are row groups, not the level-1 item_name. Infer the parent category above them. "
                    "Prefer a phrase explicitly supported by card title or nearby context over a generic shared section label. "
                    "If rule_based_hint is already a more specific domain/category than default_item_name, prefer that level of specificity."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        **_responses_create_kwargs(model_name),
    )
    raw_content = (getattr(response, "output_text", None) or "").strip()
    parsed = _extract_json_payload(raw_content)
    item_name = ""
    if isinstance(parsed, dict):
        item_name = _normalize_requirement_text(str(parsed.get("item_name") or "").strip())
    default_item_name = _normalize_requirement_text(str(section_context.get("default_item_name") or ""))
    if (
        rule_based_hint
        and item_name
        and default_item_name
        and item_name == default_item_name
        and rule_based_hint != default_item_name
    ):
        item_name = rule_based_hint
    elif not item_name and rule_based_hint:
        item_name = rule_based_hint
    input_tokens, output_tokens = _extract_token_usage(response)
    usage = {
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, input_tokens, output_tokens),
    }
    return item_name, usage


def _top_table_context_from_matrix(matrix: list[list[str]], fallback_title: str = "") -> tuple[str, int]:
    if not matrix:
        return "", 0

    title_norm = _normalize_requirement_text(fallback_title)
    for row_idx, row in enumerate(matrix[:2]):
        normalized_row = [_normalize_requirement_text(str(cell or "")) for cell in row]
        non_empty = [cell for cell in normalized_row if cell]
        unique_non_empty = []
        seen: set[str] = set()
        for cell in non_empty:
            if cell in seen:
                continue
            seen.add(cell)
            unique_non_empty.append(cell)
        if len(unique_non_empty) != 1:
            continue
        candidate = unique_non_empty[0]
        if title_norm and candidate == title_norm:
            continue
        if len(non_empty) < 2:
            continue
        return candidate, row_idx + 1
    return "", 0


def _split_heading_by_body_start_markers(text: str, markers: tuple[str, ...]) -> tuple[str, str] | None:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return None
    marker_matches = [
        match.start()
        for marker in markers
        for match in [re.search(re.escape(marker), normalized)]
        if match and match.start() > 0
    ]
    if not marker_matches:
        return None
    split_idx = min(marker_matches)
    heading = _normalize_requirement_text(normalized[:split_idx])
    body = _normalize_requirement_text(normalized[split_idx:])
    if len(re.sub(r"\s+", "", heading)) <= 2:
        return None
    if heading and body:
        return heading, body
    return None


def _split_inline_heading_and_body(text: str) -> tuple[str, list[str]]:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return "", []

    special_heading_match = re.match(
        r"^(?P<heading>※\s*[^\.。\n]+?)\s+(?P<body>(?:본 사업은|해당 사업은|본 프로젝트는|본 프로젝트의|본 프로젝트와 관련한|제안업체의|제안사의|수행사의|납품 솔루션은|당사는|구축 완료 이후|추후 ).+)$",
        normalized,
    )
    if special_heading_match:
        return (
            _normalize_requirement_text(special_heading_match.group("heading")),
            [_normalize_requirement_text(special_heading_match.group("body"))],
        )

    split_result = _split_heading_by_body_start_markers(normalized, _COMMON_INLINE_BODY_START_MARKERS)
    if split_result:
        heading, body = split_result
        return heading, [body]
    return normalized, []


def _split_chained_numbered_heading_tail(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized or "\n" in normalized:
        return normalized
    heading_start_pattern = re.compile(
        r"(?<!\S)(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+){1,}\.?)\s*(?=\s+)",
        flags=re.IGNORECASE,
    )
    matches = list(heading_start_pattern.finditer(normalized))
    if len(matches) < 2:
        return normalized
    tail = _normalize_requirement_text(normalized[matches[1].start():])
    return tail or normalized


def _fix_concatenated_numbered_heading_rows(rows: list[dict]) -> list[dict]:
    fixed_rows: list[dict] = []
    for row in rows:
        fixed_row = dict(row)
        detail = _normalize_requirement_text(str(fixed_row.get("detail_requirement") or ""))
        if detail:
            tail = _split_chained_numbered_heading_tail(detail)
            if tail != detail:
                fixed_row["detail_requirement"] = tail
        fixed_rows.append(fixed_row)
    return fixed_rows


def _split_inline_shared_root_lines(lines: list[str]) -> list[str]:
    expanded: list[str] = []
    inline_root_pattern = re.compile(
        r"^(?P<prefix>\s*(?:[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+|\(?\d+(?:\.\d+)*[\)\.]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.])\s+.+?)\s+(?P<marker>[○●◦•□▪■◆▶Oo])\s+(?P<rest>.+)$",
        flags=re.IGNORECASE,
    )
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if not normalized:
            continue
        normalized = _split_chained_numbered_heading_tail(normalized)
        match = inline_root_pattern.match(normalized)
        if match and _normalize_requirement_text(str(match.group("rest") or "")):
            prefix = _normalize_requirement_text(str(match.group("prefix") or ""))
            marker = _normalize_requirement_text(str(match.group("marker") or ""))
            rest = _normalize_requirement_text(str(match.group("rest") or ""))
            if prefix and marker and rest:
                expanded.append(prefix)
                expanded.append(f"{marker} {rest}".strip())
                continue
        expanded.append(normalized)
    return expanded


def _split_any_inline_korean_bullet_line(line: str) -> list[str]:
    normalized = _normalize_requirement_text(line)
    if not normalized:
        return []
    marker_pattern = re.compile(
        r"(?P<head>.+?)(?P<sep>\s+)(?P<marker>(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]|①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩)\s+(?P<tail>.+)",
        flags=re.IGNORECASE,
    )
    match = marker_pattern.search(normalized)
    if not match:
        return [normalized]
    head = _normalize_requirement_text(str(match.group("head") or ""))
    marker = _normalize_requirement_text(str(match.group("marker") or ""))
    tail = _normalize_requirement_text(str(match.group("tail") or ""))
    if not head or not marker or not tail:
        return [normalized]
    
    is_korean_letter = re.match(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]", marker)
    if is_korean_letter:
        if re.match(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s*", head):
            return [head, f"{marker} {tail}".strip()]
        return [normalized]
    else:
        return [head, f"{marker} {tail}".strip()]


def _split_body_heading_and_detail_lines(text: str) -> tuple[str, list[str]]:
    normalized = _merge_quoted_numeric_reference_spans(text)
    if not normalized:
        return "", []

    numbered_heading_match = re.match(
        r"^(?P<prefix>(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?))\s+(?P<rest>.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if numbered_heading_match:
        prefix = _normalize_requirement_text(numbered_heading_match.group("prefix"))
        rest = _normalize_requirement_text(numbered_heading_match.group("rest"))
        split_result = _split_heading_by_body_start_markers(rest, _COMMON_INLINE_BODY_START_MARKERS)
        if split_result:
            heading_rest, body = split_result
            if prefix and heading_rest and body and _is_title_like_requirement_text(f"{prefix} {heading_rest}".strip()):
                return f"{prefix} {heading_rest}".strip(), [body]

    if normalized.startswith("※"):
        split_result = _split_heading_by_body_start_markers(
            normalized,
            _COMMON_INLINE_BODY_START_MARKERS,
        )
        if split_result:
            heading, body = split_result
            if heading and body:
                return heading, [body]

    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return "", []
    if len(lines) >= 2:
        first_line = _normalize_requirement_text(lines[0])
        second_line = _normalize_requirement_text(lines[1])
        if (
            re.match(r"^[\-*·ㆍ−–—]+\s*.+$", first_line)
            and re.match(r"^M\s*-\s*\d+(?:\s*(?:개월|주|일|개월차|주차|월|년|단계|시점|전|후))?.+$", second_line, flags=re.IGNORECASE)
        ):
            merged_first = _normalize_requirement_text(f"{first_line} {second_line}")
            return merged_first, lines[2:]
        return lines[0], lines[1:]

    heading, inline_details = _split_inline_heading_and_body(lines[0])
    return heading, inline_details


def _split_numbered_heading_inline_body_lines(lines: list[str]) -> list[str]:
    expanded: list[str] = []
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if not normalized:
            continue
        heading, detail_lines = _split_body_heading_and_detail_lines(normalized)
        if heading and detail_lines and heading != normalized and _is_title_like_requirement_text(heading):
            expanded.append(heading)
            expanded.extend(detail_lines)
            continue
        expanded.append(normalized)
    return expanded


def _split_rows_with_inline_numbered_heading(rows: list[dict]) -> list[dict]:
    expanded_rows: list[dict] = []
    for row in rows:
        detail_requirement = _normalize_requirement_text(str(row.get("detail_requirement") or ""))
        detail_requirement = _split_chained_numbered_heading_tail(detail_requirement)
        heading, detail_lines = _split_body_heading_and_detail_lines(detail_requirement)
        heading = _normalize_requirement_text(heading)
        detail_lines = [_normalize_requirement_text(line) for line in detail_lines if _normalize_requirement_text(line)]
        if heading and heading != detail_requirement and detail_lines and _is_title_like_requirement_text(heading):
            first_row = dict(row)
            first_row["detail_requirement"] = heading
            expanded_rows.append(first_row)

            second_row = dict(row)
            second_row["requirement"] = heading
            second_row["detail_requirement"] = _normalize_requirement_text("\n".join(detail_lines))
            expanded_rows.append(second_row)
            continue
        expanded_rows.append(row)
    return expanded_rows


def _split_inline_korean_letter_requirements(lines: list[str]) -> list[str]:
    expanded: list[str] = []
    korean_letter_marker = r"(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)"
    pattern = re.compile(rf"^\s*({korean_letter_marker})[\.\)]\s+")
    for line in lines:
        normalized = _normalize_middle_dot_connectors(line)
        if not normalized:
            continue
        if _has_inline_middle_dot_connector(normalized) and not re.match(
            r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
            normalized,
            flags=re.IGNORECASE,
        ):
            expanded.append(normalized)
            continue
        if _looks_like_sentence_text(normalized) and not re.match(
            r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
            normalized,
            flags=re.IGNORECASE,
        ):
            expanded.append(normalized)
            continue

        leading_dash_match = re.match(r"^(?P<head>[\-*·ㆍ−–—]+\s*.+?)(?P<marker>(?:%s)[\.\)]\s+.+)$" % korean_letter_marker, normalized)
        if leading_dash_match:
            head = _normalize_requirement_text(leading_dash_match.group("head"))
            marker_tail = _normalize_requirement_text(leading_dash_match.group("marker"))
            if head and marker_tail:
                expanded.append(head)
                expanded.extend(_split_inline_korean_letter_requirements([marker_tail]))
                continue

        match = pattern.match(normalized)
        if not match:
            expanded.append(normalized)
            continue
        expanded.append(normalized)
    return expanded


def _should_prefer_llm_for_body_rows(plain_text: str) -> bool:
    normalized = _normalize_requirement_text(plain_text)
    if not normalized:
        return False
    inline_korean_letter_runs = re.findall(r"(?:^|\s)(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)[\.\)](?:\s+(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)[\.\)])+", normalized)
    if inline_korean_letter_runs:
        return True
    return False


def _is_brief_heading_phrase(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    if re.match(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+\s*", normalized):
        return False
    if _looks_like_sentence_text(normalized):
        return False
    stripped_numbering = _strip_leading_numbering(normalized) if _has_leading_numbering(normalized) else normalized
    compact = _normalize_requirement_text(stripped_numbering)
    if not compact:
        return False
    return len(compact) <= 40


def _should_skip_llm_for_heading_only_body_card(plain_text: str, card_title: str = "") -> bool:
    normalized = _normalize_requirement_text(plain_text)
    if not normalized:
        return True

    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    normalized_title = _normalize_requirement_text(card_title)
    if normalized_title and lines and _normalize_requirement_text(lines[0]) == normalized_title:
        lines = lines[1:]
    if not lines:
        return True

    # If the remaining body is just a short list of heading phrases without
    # bullets or sentence-like details, LLM tends to invent explanatory prose.
    if all(_is_brief_heading_phrase(line) for line in lines):
        return True
    return False


def _body_line_raw_level(text: str) -> int | None:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return None
    if _is_reference_like_body_line(normalized):
        return None
    if re.match(r"^[Oo]\s+", normalized):
        return 1
    if re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|[IVXLCDM]+[\.\)]|\d+(?:\.\d+)+)\s*", normalized, flags=re.IGNORECASE):
        return 1
    if re.match(r"^[\-*·ㆍ−–—]+\s*", normalized):
        return 2
    if re.match(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s*", normalized):
        return 3
    if re.match(r"^(?:\(?\d+\)|\d+[\.\)])\s*", normalized):
        return 4
    if re.match(r"^(?:[A-Za-z][\.\)]|[•▪■◆▶◇□※⦁◦○])\s*", normalized, flags=re.IGNORECASE):
        return 5
    return None


def _bullet_family(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return "other"
    if re.match(r"^[Oo]\s+", normalized):
        return "symbol"
    if re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|[IVXLCDM]+[\.\)]|\d+(?:\.\d+)+)\s*", normalized, flags=re.IGNORECASE):
        return "heading"
    if re.match(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)[\.\)]\s*", normalized):
        return "hangul_syllable"
    if re.match(r"^[ㄱ-ㅎ][\.\)]\s*", normalized):
        return "hangul_jamo"
    if re.match(r"^\d+\.\s*", normalized):
        return "digit_dot"
    if re.match(r"^\(?\d+\)\s*", normalized):
        return "digit_paren"
    if re.match(r"^(?:[A-Za-z]\.\s+|[A-Za-z]\)\s+)", normalized):
        return "latin"
    if re.match(r"^[•▪■◆▶◇□※⦁◦○]\s*", normalized):
        return "symbol"
    if re.match(r"^[\-*·ㆍ−–—]+\s*", normalized):
        return "dash"
    return "other"


def _classify_dash_variant(prev_family: str | None, next_family: str | None) -> str:
    if next_family == "hangul_jamo":
        return "dash_mid"
    return "dash_low"


def _bullet_level_from_family(family: str) -> int:
    return {
        "heading": 1,
        "hangul_syllable": 2,
        "dash_mid": 3,
        "hangul_jamo": 4,
        "digit_dot": 5,
        "digit_paren": 6,
        "latin": 7,
        "symbol": 8,
        "dash_low": 9,
        "other": 10,
    }.get(family, 10)


def _profiled_bullet_family(text: str, *, next_text: str = "") -> str:
    family = _bullet_family(text)
    if family != "dash":
        return family
    next_family = _bullet_family(next_text)
    if next_family in {"hangul_jamo", "digit_dot", "digit_paren", "latin", "symbol"}:
        return "dash_mid"
    return "dash_low"


def _infer_section_body_hierarchy_profile(section_name: str, cards: list[RfpCard]) -> dict:
    family_sequences: list[list[str]] = []
    for card in cards:
        lines = _html_excerpt_lines(str(getattr(card, "html_excerpt", "") or ""))
        if not lines:
            continue
        card_title = _normalize_requirement_text(
            str(getattr(card, "subject", None) or getattr(card, "requirement", None) or "")
        )
        if card_title and _normalize_requirement_text(lines[0]) == card_title:
            lines = lines[1:]
        lines = _merge_bullet_continuation_lines(lines)
        lines = _strip_da_marker_when_no_ga_na(lines)
        lines = _merge_plain_sentence_wraps(lines)
        lines = _split_inline_shared_root_lines(lines)
        sequence: list[str] = []
        for idx, line in enumerate(lines):
            normalized_line = _normalize_requirement_text(line)
            if not normalized_line or _is_section_reference_note_line(normalized_line) or _is_reference_like_body_line(normalized_line):
                continue
            next_text = _normalize_requirement_text(lines[idx + 1]) if idx + 1 < len(lines) else ""
            family = _profiled_bullet_family(normalized_line, next_text=next_text)
            if family == "other":
                continue
            if not sequence or sequence[-1] != family:
                sequence.append(family)
        if sequence:
            family_sequences.append(sequence)

    normalized_section = _normalize_requirement_text(section_name)
    if not family_sequences:
        return {
            "family_sequence": [],
            "family_level_map": {},
            "root_family": "",
            "requirement_family": "",
            "detail_family": "",
            "uses_shared_parent_root": False,
            "uses_dash_item_root": False,
            "no_hierarchy": True,
            "section_name": normalized_section,
        }

    ordered_families: list[str] = []
    max_depth = max(len(seq) for seq in family_sequences)
    for position in range(max_depth):
        position_counts = Counter(
            seq[position]
            for seq in family_sequences
            if position < len(seq) and seq[position]
        )
        for family, _ in position_counts.most_common():
            if family not in ordered_families:
                ordered_families.append(family)
                break

    if not ordered_families:
        ordered_families = [family_sequences[0][0]]

    family_level_map = {family: idx + 1 for idx, family in enumerate(ordered_families)}
    root_family = ordered_families[0] if ordered_families else ""
    requirement_family = ordered_families[1] if len(ordered_families) > 1 else ""
    detail_family = ordered_families[2] if len(ordered_families) > 2 else ""

    return {
        "family_sequence": ordered_families,
        "family_level_map": family_level_map,
        "root_family": root_family,
        "requirement_family": requirement_family,
        "detail_family": detail_family,
        "uses_shared_parent_root": root_family == "symbol",
        "uses_dash_item_root": root_family in {"dash_mid", "dash_low"},
        "no_hierarchy": False,
        "section_name": normalized_section,
    }


def _profiled_bullet_level_from_family(family: str, section_profile: dict | None = None, *, next_text: str = "") -> int:
    profile = section_profile or {}
    level_map = profile.get("family_level_map") or {}
    normalized_family = family
    if normalized_family == "dash":
        next_family = _profiled_bullet_family(next_text)
        if next_family in {"hangul_jamo", "digit_dot", "digit_paren", "latin", "symbol"}:
            normalized_family = "dash_mid"
        else:
            normalized_family = "dash_low"
    if normalized_family in level_map:
        return int(level_map.get(normalized_family) or 10)
    if normalized_family == "symbol" and profile.get("root_family") == "symbol":
        return 1
    return _bullet_level_from_family(normalized_family)


def _normalize_shared_parent_item_name(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    return normalized


def _leading_bullet_prefix(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    first_line = next((line.strip() for line in normalized.split("\n") if line.strip()), "")
    if not first_line:
        return ""
    match = re.match(r"^(?P<prefix>[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+)\s*(?P<body>.+)$", first_line)
    if match and _normalize_requirement_text(str(match.group("body") or "")):
        return str(match.group("prefix") or "").strip()
    return ""


def _strip_leading_bullet_prefix(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    lines = normalized.split("\n")
    first_idx = next((idx for idx, line in enumerate(lines) if line.strip()), None)
    if first_idx is None:
        return ""
    first_line = lines[first_idx].strip()
    prefix = _leading_bullet_prefix(first_line)
    if not prefix:
        return normalized
    stripped_first = re.sub(r"^[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+\s*", "", first_line, count=1).strip()
    if not stripped_first:
        return normalized
    lines[first_idx] = stripped_first
    return "\n".join(lines).strip()


def _infer_dash_mid_item_name_from_text(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 2:
        return ""
    first_dash_index = next(
        (idx for idx, line in enumerate(lines) if re.match(r"^[\-*·ㆍ−–—]+\s*", _normalize_requirement_text(line))),
        None,
    )
    if first_dash_index is None or first_dash_index + 1 >= len(lines):
        return ""
    next_line = _normalize_requirement_text(lines[first_dash_index + 1])
    if not re.match(r"^ㄱ[\.\)]\s*", next_line):
        return ""
    return _normalize_requirement_text(lines[first_dash_index])


def _extract_atomic_body_rows(
    lines: list[str],
    *,
    title: str = "",
    default_item_name: str = "",
    build_method: str = "룰 기반(본문 계층 정규화)",
) -> list[dict]:
    normalized_title = _normalize_requirement_text(title)
    default_level1 = _normalize_requirement_text(default_item_name) or normalized_title
    if not default_level1:
        return []
    base_requirement = normalized_title or default_level1
    rows: list[dict] = []
    for line in lines:
        normalized_line = _normalize_requirement_text(line)
        if not normalized_line or _is_only_page_info(normalized_line):
            continue
        if normalized_title and normalized_line == normalized_title:
            continue
        rows.append(
            {
                "item_name": default_level1,
                "requirement": base_requirement,
                "detail_requirement": normalized_line,
                "result_note": "",
                "build_method": build_method,
                "build_source_detail": "계층 없음 / atomic",
            }
        )
    return rows


def _has_symbol_parent_section_line(lines: list[str]) -> bool:
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if re.match(r"^[○●◦•□▪■◆▶Oo]\s+\S", normalized):
            return True
    return False


def _infer_korean_heading_before_dash_detail(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 2:
        return ""
    heading_pattern = re.compile(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s+")
    dash_pattern = re.compile(r"^[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+\s*")
    for idx, line in enumerate(lines[:-1]):
        if not heading_pattern.match(line):
            continue
        next_line = lines[idx + 1]
        if dash_pattern.match(next_line) or _has_inline_middle_dot_connector(next_line):
            return _normalize_requirement_text(line)
    return ""


def _should_override_requirement_with_korean_heading(build_source: str) -> bool:
    return _normalize_requirement_text(build_source) == "본문"


def _extract_repeated_dash_item_rows(lines: list[str], *, title: str = "", default_item_name: str = "", build_method: str = "룰 기반") -> list[dict]:
    normalized_lines = [_normalize_requirement_text(line) for line in lines if _normalize_requirement_text(line)]
    if not normalized_lines:
        return []

    normalized_title = _normalize_requirement_text(title)
    work_lines = normalized_lines[:]
    if normalized_title and work_lines and _normalize_requirement_text(work_lines[0]) == normalized_title:
        work_lines = work_lines[1:]
    if len(work_lines) < 2:
        return []

    korean_req_pattern = re.compile(r"^[ㄱ-ㅎ][\.\)]\s*")

    dash_blocks: list[int] = []
    for idx, line in enumerate(work_lines):
        if not re.match(r"^[\-*·ㆍ−–—]+\s*.+$", line):
            continue
        if idx + 1 >= len(work_lines) or not korean_req_pattern.match(_normalize_requirement_text(work_lines[idx + 1])):
            continue
        dash_blocks.append(idx)

    if not dash_blocks:
        return []

    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    prelude_rows: list[dict] = []
    prelude_seen: set[tuple[str, str, str]] = set()
    first_dash_block_idx = dash_blocks[0]
    for idx, line in enumerate(work_lines[:first_dash_block_idx]):
        if not re.match(r"^[\-*·ㆍ−–—]+\s*.+$", line):
            continue
        detail_text = _normalize_requirement_text(line)
        if not detail_text or _is_only_page_info(detail_text):
            continue
        key = (
            _normalize_requirement_text(default_item_name) or normalized_title or _normalize_requirement_text(work_lines[0]),
            normalized_title or _normalize_requirement_text(default_item_name) or _normalize_requirement_text(work_lines[0]),
            detail_text,
        )
        if key in prelude_seen:
            continue
        prelude_seen.add(key)
        prelude_rows.append(
            {
                "item_name": _normalize_requirement_text(default_item_name) or normalized_title or _normalize_requirement_text(work_lines[0]),
                "requirement": normalized_title or _normalize_requirement_text(default_item_name) or _normalize_requirement_text(work_lines[0]),
                "detail_requirement": detail_text,
                "result_note": "",
                "build_method": build_method,
                "build_source_detail": "계층 없음 / atomic",
            }
        )
    for block_idx, item_idx in enumerate(dash_blocks):
        block_end = dash_blocks[block_idx + 1] if block_idx + 1 < len(dash_blocks) else len(work_lines)
        item_name = _normalize_requirement_text(work_lines[item_idx])
        requirement_indices = [
            idx
            for idx in range(item_idx + 1, block_end)
            if korean_req_pattern.match(_normalize_requirement_text(work_lines[idx]))
        ]
        if not requirement_indices:
            continue
        for req_pos, req_idx in enumerate(requirement_indices):
            next_req_idx = requirement_indices[req_pos + 1] if req_pos + 1 < len(requirement_indices) else block_end
            requirement_text = _normalize_requirement_text(work_lines[req_idx])
            detail_lines = work_lines[req_idx + 1:next_req_idx]
            if re.fullmatch(r"^[ㄱ-ㅎ][\.\)]", requirement_text) and detail_lines:
                first_detail_line = _normalize_requirement_text(detail_lines[0])
                if first_detail_line and not re.match(r"^[ㄱ-ㅎ][\.\)]\s*", first_detail_line):
                    requirement_text = _normalize_requirement_text(f"{requirement_text} {first_detail_line}")
                    detail_lines = detail_lines[1:]
            detail_units: list[str] = []
            for line in detail_lines:
                detail_units.extend(_split_atomic_detail_units(line) or [line])
            if not detail_units:
                detail_units = [requirement_text]
            for detail_unit in detail_units:
                cleaned = _strip_trailing_orphan_bullet(_normalize_requirement_text(detail_unit))
                if not cleaned or _is_only_page_info(cleaned):
                    continue
                cleaned = _normalize_requirement_text(cleaned)
                key = (item_name, requirement_text, cleaned)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "item_name": item_name,
                        "requirement": requirement_text,
                        "detail_requirement": cleaned,
                        "result_note": "",
                        "build_method": build_method,
                        "build_source_detail": "항목명, 요구사항, 상세요건",
                    }
                )
    return prelude_rows + rows


def _is_numbered_body_hierarchy_line(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    if _is_reference_like_body_line(normalized):
        return False
    return bool(
        re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s*", normalized, flags=re.IGNORECASE)
        or re.match(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s*", normalized)
        or re.match(r"^(?:\(?\d+\)|\d+[\.\)])\s*", normalized)
        or re.match(r"^[\-*•▪■◆▶◦○□◇·ㆍ⦁−–—※]+\s*", normalized)
    )


def _extract_ga_number_dash_star_body_rows(
    lines: list[str],
    *,
    title: str = "",
    default_item_name: str = "",
    build_method: str = "룰 기반(본문 계층 정규화)",
) -> list[dict]:
    return []


def _extract_special_hierarchical_body_rows(
    plain_text: str,
    *,
    title: str = "",
    default_item_name: str = "",
    section_context: dict | None = None,
) -> list[dict]:
    return []


def _summarize_body_rule_debug(
    plain_text: str,
    *,
    title: str = "",
    default_item_name: str = "",
    section_context: dict | None = None,
) -> dict:
    normalized = _normalize_requirement_text(plain_text)
    normalized_title = _normalize_requirement_text(title)
    raw_lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    special_rule_candidates: list[str] = []
    matched_special_rule = ""

    content_lines = raw_lines[:]
    if normalized_title:
        content_lines = [line for line in content_lines if _normalize_requirement_text(line) != normalized_title]

    bullet_kind_sequence: list[str] = []
    bullet_kind_counts: dict[str, int] = {}
    for line in content_lines:
        normalized_line = _normalize_requirement_text(line)
        if not normalized_line:
            continue
        bullet_kind = _bullet_family(normalized_line)
        if bullet_kind == "other":
            continue
        bullet_kind_counts[bullet_kind] = bullet_kind_counts.get(bullet_kind, 0) + 1
        if bullet_kind not in bullet_kind_sequence:
            bullet_kind_sequence.append(bullet_kind)

    bullet_kind_count = len(bullet_kind_sequence)
    if bullet_kind_count <= 0:
        general_rule_hint = "불릿없음"
    elif bullet_kind_count >= 5:
        general_rule_hint = "불릿5+"
    else:
        general_rule_hint = f"불릿{bullet_kind_count}"

    applied_rule_display = "본문룰(계층 없음 / atomic)"

    return {
        "special_rule_candidates": special_rule_candidates,
        "special_rule_matched": matched_special_rule,
        "general_rule_hint": general_rule_hint,
        "applied_rule_display": applied_rule_display,
        "bullet_kind_count": bullet_kind_count,
        "bullet_kind_counts": bullet_kind_counts,
        "bullet_kind_sequence_preview": bullet_kind_sequence[:20],
        "bullet_kind_first_seen_order": bullet_kind_sequence[:20],
        "line_preview": raw_lines[:20],
    }


def _select_general_rule_hint(level_counts: dict[str, int], check_order: list[str] | tuple[str, ...] | None = None) -> str:
    order = list(check_order or ["5+", "4", "3", "2", "1"])
    for label in order:
        if level_counts.get(label, 0) > 0:
            return f"불릿{label}"
    return "불릿없음"


def _format_body_rule_display(special_rule_matched: str, general_rule_hint: str) -> str:
    return "본문룰(계층 없음 / atomic)"


def _is_only_page_info(text: str) -> bool:
    if not text:
        return False
    val = text.strip()
    val = re.sub(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*\s]+", "", val)
    val = val.strip().lower()
    num_part = r"(?:of|[\d\s\-\~\,\.\(\)\/\+\&])+"
    patterns = [
        rf"^p\.?\s*{num_part}$",
        rf"^page\s*{num_part}$",
        rf"^{num_part}p$",
        rf"^{num_part}페이지$",
        rf"^페이지\s*{num_part}$",
        rf"^\d+$",
        rf"^\d+\s*[\-\~]\s*\d+$"
    ]
    combined = "|".join(patterns)
    return bool(re.match(combined, val))


def _is_section_reference_note_line(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    if not re.match(r"^[※*]\s*\S+", normalized):
        return False
    compact = re.sub(r"\s+", "", normalized)
    note_hints = [
        "사업내용이변경될경우",
        "과업변경절차",
        "상기요구사항",
        "제안범위에포함",
        "추가제안가능",
        "본RFP에서제시한기능",
        "대체가능한기능",
        "구현방안을포함",
        "필수적이라판단되는사항",
    ]
    return any(hint in compact for hint in note_hints) or len(compact) >= 25


def _is_reference_like_body_line(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    if _is_section_reference_note_line(normalized):
        return True
    if re.search(r"(?:참고|참조)하시기바랍니다?", compact):
        return True
    if re.search(r"(?:을|를)참고(?:하시기바랍니다?)?", compact):
        return True
    if re.search(r"별첨\d+(?:\.\d+)*", compact):
        return True
    if re.search(r"『\d+(?:\.\d+)+[^』]*』?\s*을?참고", compact):
        return True
    return False


def _has_inline_numeric_bullet_not_at_start(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    return bool(
        re.search(
            r"(?<!^)(?:^|\s)(?:\(?\d+(?:\.\d+)*[\)\.\-])\s+",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _is_lonely_korean_bullet_marker_line(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    return bool(re.fullmatch(r"(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s*", normalized))


def _strip_leading_korean_bullet_marker(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    return re.sub(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s*", "", normalized)


def _strip_da_marker_when_no_ga_na(lines: list[str]) -> list[str]:
    has_ga_na = any(
        re.match(r"^(?:가|나)[\.\)]\s*", _normalize_requirement_text(line))
        for line in lines
    )
    if has_ga_na:
        return lines
    # 한 줄짜리 "다."는 본문 머리표일 수 있으니 그대로 둔다.
    if len(lines) <= 1:
        return lines
    adjusted: list[str] = []
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if re.match(r"^다[\.\)]\s+", normalized):
            normalized = re.sub(r"^다[\.\)]\s*", "", normalized)
        adjusted.append(normalized)
    return adjusted


def _merge_plain_sentence_wraps(lines: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    while idx < len(lines):
        line = _normalize_requirement_text(lines[idx])
        if not line:
            idx += 1
            continue
        if not merged:
            merged.append(line)
            idx += 1
            continue

        prev = merged[-1]
        current_has_marker = bool(
            re.match(
                r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
                line,
                flags=re.IGNORECASE,
            )
        )
        prev_has_marker = bool(
            re.match(
                r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
                prev,
                flags=re.IGNORECASE,
            )
        )
        prev_looks_open = bool(re.search(r"[가-힣A-Za-z0-9]$", prev)) and not re.search(r"[\.!?。:：]$", prev)

        if not prev_has_marker and not current_has_marker and prev_looks_open:
            merged[-1] = f"{prev} {line}".strip()
            idx += 1
            continue
        merged.append(line)
        idx += 1
    return merged


def _merge_bullet_continuation_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    while idx < len(lines):
        raw_line = lines[idx]
        line = _normalize_requirement_text(raw_line)
        if not line:
            idx += 1
            continue
        if not merged:
            merged.append(line)
            idx += 1
            continue

        prev = merged[-1]
        prev_is_heading_like = _is_heading_like_text(prev) or _is_title_like_requirement_text(prev)
        line_looks_like_sentence = _looks_like_sentence_text(line)
        prev_has_marker = bool(
            re.match(
                r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
                prev,
                flags=re.IGNORECASE,
            )
        )
        line_is_schedule_marker = bool(
            re.match(r"^\s*[A-Z]\s*-\s*\d+(?:\s*(?:개월|주|일|개월차|주차|월|년|단계|시점|전|후))?\b", line)
        )
        line_has_marker = bool(
            re.match(
                r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
                line,
                flags=re.IGNORECASE,
            )
        )
        if line_is_schedule_marker:
            line_has_marker = False
        line_is_note = _is_section_reference_note_line(line) or _is_reference_like_body_line(line)
        prev_looks_open = bool(re.search(r"[가-힣A-Za-z0-9]$", prev)) and not re.search(r"[\.!?。:：]$", prev)

        # Keep a heading line and its following sentence as separate units.
        # Otherwise HTML heading blocks like `<h4>1.1. 프로젝트 개요</h4><p>...</p>`
        # can collapse into one line, and quoted references such as `『1.2. ...』`
        # may later be misread as inline numbered markers.
        if prev_is_heading_like and line_looks_like_sentence:
            merged.append(line)
            idx += 1
            continue

        if _is_lonely_korean_bullet_marker_line(line):
            if prev_looks_open:
                merged[-1] = f"{prev} {line}".strip()
                idx += 1
                continue
            next_line = _normalize_requirement_text(lines[idx + 1]) if idx + 1 < len(lines) else ""
            if next_line and not _is_lonely_korean_bullet_marker_line(next_line) and not line_has_marker:
                merged.append(f"{line} {next_line}".strip())
                idx += 2
                continue
        if prev_has_marker and not line_has_marker and not line_is_note and prev_looks_open:
            merged[-1] = f"{prev} {line}".strip()
            idx += 1
            continue
        merged.append(line)
        idx += 1
    return merged



def _extract_hierarchical_body_rows(
    plain_text: str,
    title: str = "",
    default_item_name: str = "",
    section_context: dict | None = None,
) -> list[dict]:
    normalized = _normalize_requirement_text(plain_text)
    if not normalized:
        return []
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return []
    default_level1 = _normalize_requirement_text(default_item_name) or _normalize_requirement_text(title) or lines[0]
    return _extract_atomic_body_rows(
        lines,
        title=title,
        default_item_name=default_level1,
        build_method="룰 기반(단일 행 본문)",
    )

    section_profile = (section_context or {}).get("body_hierarchy_profile") or {}
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    raw_lines = lines[:]

    if section_profile.get("no_hierarchy"):
        return _extract_atomic_body_rows(
            raw_lines,
            title=title,
            default_item_name=default_item_name,
            build_method="룰 기반(본문 계층 정규화)",
        )

    lines = _merge_bullet_continuation_lines(lines)
    lines = _strip_da_marker_when_no_ga_na(lines)
    lines = _merge_plain_sentence_wraps(lines)
    lines = _split_numbered_heading_inline_body_lines(lines)
    if len(lines) == 1:
        expanded_lines = _split_inline_korean_letter_requirements(lines)
        if len(expanded_lines) > 1:
            lines = expanded_lines
        else:
            detail = _normalize_requirement_text(lines[0])
            if detail:
                if _is_only_page_info(detail):
                    return []
                normalized_title = _normalize_requirement_text(title)
                default_level1 = _normalize_requirement_text(default_item_name) or normalized_title or detail
                base_requirement = normalized_title or default_level1
                if not _is_numbered_body_hierarchy_line(detail):
                    return [
                        {
                            "item_name": default_level1,
                            "requirement": base_requirement,
                            "detail_requirement": detail,
                            "result_note": "",
                            "build_method": "룰 기반(단일 행 본문)",
                            "build_source_detail": "계층 없음 / atomic",
                        }
                    ]
                detail_units = _split_atomic_detail_units(detail)
                if len(detail_units) > 1:
                    first_unit = _normalize_requirement_text(detail_units[0])
                    if first_unit and _has_leading_numbering(first_unit):
                        default_level1 = first_unit
                        base_requirement = normalized_title or default_level1
                    if _normalize_requirement_text(detail_units[0]) == default_level1:
                        detail_units = detail_units[1:]
                    rows: list[dict] = []
                    seen: set[tuple[str, str, str]] = set()
                    for detail_unit in detail_units:
                        cleaned_detail = _strip_trailing_orphan_bullet(_normalize_requirement_text(detail_unit))
                        if not cleaned_detail or _is_only_page_info(cleaned_detail):
                            continue
                        key = (default_level1, base_requirement, cleaned_detail)
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append(
                            {
                                "item_name": default_level1,
                                "requirement": base_requirement,
                                "detail_requirement": cleaned_detail,
                                "result_note": "",
                                "build_method": "룰 기반(단일 행 본문)",
                                "build_source_detail": "계층 없음 / atomic",
                            }
                        )
                    if rows:
                        return rows
                return [
                    {
                        "item_name": default_level1,
                        "requirement": base_requirement,
                        "detail_requirement": detail,
                        "result_note": "",
                        "build_method": "룰 기반(단일 행 본문)",
                        "build_source_detail": "계층 없음 / atomic",
                    }
                ]
            return []
    if len(lines) < 1:
        return []
    lines = _split_inline_shared_root_lines(lines)
    split_lines: list[str] = []
    for line in lines:
        split_lines.extend(_split_any_inline_korean_bullet_line(line))
    lines = _split_inline_korean_letter_requirements(split_lines)
    lines = [
        _collapse_repeated_token_tail(line)
        for line in lines
        if _normalize_requirement_text(line) and not (_is_section_reference_note_line(line) or _is_reference_like_body_line(line))
    ]

    normalized_title = _normalize_requirement_text(title)
    work_lines = lines[:]
    if normalized_title and work_lines and _normalize_requirement_text(work_lines[0]) == normalized_title:
        work_lines = work_lines[1:]
    if len(work_lines) < 1:
        return []

    repeated_dash_rows: list[dict] = []
    if section_profile.get("uses_dash_item_root"):
        repeated_dash_rows = _extract_repeated_dash_item_rows(
            work_lines,
            title=normalized_title,
            default_item_name=default_item_name,
            build_method="룰 기반(본문 계층 정규화)",
        )
    if repeated_dash_rows:
        return repeated_dash_rows

    default_level1 = _normalize_requirement_text(default_item_name) or normalized_title or _normalize_requirement_text(lines[0])
    if not default_level1:
        return []

    first_line_text = work_lines[0]
    if _is_title_like_requirement_text(first_line_text):
        leading_item_index = next(
            (
                idx
                for idx, line in enumerate(work_lines[1:], start=1)
                if _normalize_requirement_text(line) and re.match(r"^[\-*·ㆍ−–—]+\s*", _normalize_requirement_text(line))
            ),
            None,
        )
        if leading_item_index is not None:
            if leading_item_index + 1 < len(work_lines) and re.match(r"^ㄱ[\.\)]\s*", _normalize_requirement_text(work_lines[leading_item_index + 1])):
                first_numbered_index = leading_item_index + 1
            else:
                first_numbered_index = None
            if first_numbered_index is not None:
                prelude_rows: list[dict] = []
                prelude_seen: set[tuple[str, str, str]] = set()
                for line in work_lines[1:leading_item_index]:
                    detail_text = _normalize_requirement_text(line)
                    if not detail_text or not re.match(r"^[\-*·ㆍ−–—]+\s*", detail_text) or _is_only_page_info(detail_text):
                        continue
                    key = (default_level1, normalized_title or default_level1, detail_text)
                    if key in prelude_seen:
                        continue
                    prelude_seen.add(key)
                    prelude_rows.append(
                        {
                            "item_name": default_level1,
                            "requirement": normalized_title or default_level1,
                            "detail_requirement": detail_text,
                            "result_note": "",
                            "build_method": "룰 기반(본문 계층 정규화)",
                        }
                    )
                item_name = _normalize_requirement_text(work_lines[leading_item_index])
                requirement_text = _collapse_repeated_token_tail(_normalize_requirement_text(work_lines[first_numbered_index]))
                rows: list[dict] = []
                seen: set[tuple[str, str, str]] = set()
                for line in work_lines[first_numbered_index + 1:]:
                    for detail_unit in _split_atomic_detail_units(line) or [line]:
                        cleaned = _strip_trailing_orphan_bullet(_normalize_requirement_text(detail_unit))
                        if not cleaned or _is_only_page_info(cleaned):
                            continue
                        cleaned = _normalize_requirement_text(cleaned)
                        key = (item_name, requirement_text, cleaned)
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append(
                            {
                                "item_name": item_name,
                                "requirement": requirement_text,
                                "detail_requirement": cleaned,
                                "result_note": "",
                                "build_method": "룰 기반(본문 계층 정규화)",
                            }
                        )
                if rows:
                    return prelude_rows + rows

    first_line_is_title_like = bool(re.match(r"^[\-*·ㆍ−–—]+\s*", first_line_text)) or not _is_numbered_body_hierarchy_line(first_line_text)
    if first_line_is_title_like and any(
        _is_numbered_body_hierarchy_line(line) for line in work_lines[1:]
    ):
        first_numbered_index = next(
            (idx for idx, line in enumerate(work_lines[1:], start=1) if _is_numbered_body_hierarchy_line(line)),
            None,
        )
        if first_numbered_index is None:
            first_numbered_index = 1
        prelude_rows: list[dict] = []
        prelude_seen: set[tuple[str, str, str]] = set()
        for line in work_lines[1:first_numbered_index]:
            detail_text = _normalize_requirement_text(line)
            if not detail_text or not re.match(r"^[\-*·ㆍ−–—]+\s*", detail_text) or _is_only_page_info(detail_text):
                continue
            key = (default_level1, normalized_title or default_level1, detail_text)
            if key in prelude_seen:
                continue
            prelude_seen.add(key)
            prelude_rows.append(
                {
                    "item_name": default_level1,
                    "requirement": normalized_title or default_level1,
                    "detail_requirement": detail_text,
                    "result_note": "",
                    "build_method": "룰 기반(본문 계층 정규화)",
                }
            )
        if re.match(r"^[ㄱ-ㅎ][\.\)]\s*", _normalize_requirement_text(work_lines[first_numbered_index])):
            leading_item_candidates = [
                _normalize_requirement_text(line)
                for line in work_lines[1:first_numbered_index]
                if _normalize_requirement_text(line) and re.match(r"^[\-*·ㆍ−–—]+\s*", _normalize_requirement_text(line))
            ]
            has_followup_korean_jamo = any(
                re.match(r"^ㄱ[\.\)]\s*", _normalize_requirement_text(line))
                for line in work_lines[first_numbered_index + 1:]
            )
            item_name = (
                leading_item_candidates[-1]
                if leading_item_candidates and has_followup_korean_jamo
                else (default_level1 or _normalize_requirement_text(work_lines[0]))
            )
            requirement_parts = [_normalize_requirement_text(work_lines[first_numbered_index])]
            detail_start_index = first_numbered_index + 1
            while detail_start_index < len(work_lines) and not _is_numbered_body_hierarchy_line(work_lines[detail_start_index]):
                requirement_parts.append(_normalize_requirement_text(work_lines[detail_start_index]))
                detail_start_index += 1
            requirement_text = _collapse_repeated_token_tail(_normalize_requirement_text(" ".join(part for part in requirement_parts if part)))
            if detail_start_index >= len(work_lines):
                return prelude_rows + [
                    {
                        "item_name": item_name,
                        "requirement": requirement_text,
                        "detail_requirement": requirement_text,
                        "result_note": "",
                        "build_method": "룰 기반(본문 계층 정규화)",
                    }
                ]
            rows: list[dict] = []
            seen: set[tuple[str, str, str]] = set()
            for line in work_lines[detail_start_index:]:
                for detail_unit in _split_atomic_detail_units(line) or [line]:
                    cleaned = _strip_trailing_orphan_bullet(_normalize_requirement_text(detail_unit))
                    if not cleaned or _is_only_page_info(cleaned):
                        continue
                    cleaned = _normalize_requirement_text(cleaned)
                    key = (item_name, requirement_text, cleaned)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "item_name": item_name,
                            "requirement": requirement_text,
                            "detail_requirement": cleaned,
                            "result_note": "",
                            "build_method": "룰 기반(본문 계층 정규화)",
                        }
                    )
                if rows:
                    return prelude_rows + rows

    # If the body contains no numbered hierarchy at all, treat every remaining
    # line as an independent detail item under the card title.
    if not any(_is_numbered_body_hierarchy_line(line) for line in work_lines):
        base_requirement = normalized_title or default_level1
        detail = _normalize_requirement_text(" ".join(line for line in work_lines if _normalize_requirement_text(line)))
        if not detail or _is_only_page_info(detail):
            return []
        return [
            {
                "item_name": default_level1,
                "requirement": base_requirement,
                "detail_requirement": detail,
                "result_note": "",
                "build_method": "룰 기반(본문 계층 정규화)",
            }
        ]

    # 본문 계층은 더 이상 트리로 재구성하지 않고, 모든 본문 라인을
    # 같은 레벨의 상세요건으로 평탄화한다.
    flat_rows: list[dict] = []
    flat_seen: set[tuple[str, str, str]] = set()
    flat_item_name = default_level1
    flat_requirement = normalized_title or default_level1
    for line in work_lines:
        normalized_line = _normalize_requirement_text(line)
        if not normalized_line or _is_only_page_info(normalized_line):
            continue
        if normalized_title and normalized_line == normalized_title:
            continue
        for detail_unit in _split_atomic_detail_units(normalized_line) or [normalized_line]:
            cleaned_detail = _strip_trailing_orphan_bullet(_normalize_requirement_text(detail_unit))
            if not cleaned_detail or _is_only_page_info(cleaned_detail):
                continue
            key = (flat_item_name, flat_requirement, cleaned_detail)
            if key in flat_seen:
                continue
            flat_seen.add(key)
            flat_rows.append(
                {
                    "item_name": flat_item_name,
                    "requirement": flat_requirement,
                    "detail_requirement": cleaned_detail,
                    "result_note": "",
                    "build_method": "룰 기반(본문 계층 정규화)",
                    "build_source_detail": "계층 없음 / atomic",
                }
            )
    return flat_rows

    roots: list[dict] = []
    stack: list[dict] = []
    last_node: dict | None = None
    intro_lines: list[str] = []
    all_levels: list[int] = []

    line_families: list[str] = []
    for idx, line in enumerate(work_lines):
        normalized_line = _normalize_requirement_text(line)
        if _is_section_reference_note_line(normalized_line) or _is_reference_like_body_line(normalized_line):
            line_families.append("other")
            continue
        raw_level = _body_line_raw_level(normalized_line)
        if raw_level is None:
            line_families.append("other")
            continue
        next_text = _normalize_requirement_text(work_lines[idx + 1]) if idx + 1 < len(work_lines) else ""
        family = _profiled_bullet_family(normalized_line, next_text=next_text)
        line_families.append(family)

    active_section_profile = dict(section_profile)
    if not (active_section_profile.get("family_level_map") or {}):
        ordered_families: list[str] = []
        for family in line_families:
            if not family or family == "other" or family in ordered_families:
                continue
            ordered_families.append(family)
        if ordered_families:
            active_section_profile = {
                **active_section_profile,
                "family_sequence": ordered_families,
                "family_level_map": {family: idx + 1 for idx, family in enumerate(ordered_families)},
                "root_family": ordered_families[0],
                "requirement_family": ordered_families[1] if len(ordered_families) > 1 else "",
                "detail_family": ordered_families[2] if len(ordered_families) > 2 else "",
                "uses_shared_parent_root": ordered_families[0] == "symbol",
                "uses_dash_item_root": ordered_families[0] in {"dash_mid", "dash_low"},
            }

    for idx, line in enumerate(work_lines):
        normalized_line = _normalize_requirement_text(line)
        raw_level = _body_line_raw_level(normalized_line)
        if raw_level is None:
            if last_node is not None:
                last_node["text"] = f"{last_node['text']}\n{line}".strip()
            else:
                intro_lines.append(line)
            continue

        family = line_families[idx] if idx < len(line_families) else _bullet_family(normalized_line)
        next_text = _normalize_requirement_text(work_lines[idx + 1]) if idx + 1 < len(work_lines) else ""
        if family == "dash":
            family = _profiled_bullet_family(normalized_line, next_text=next_text)

        current_level = _profiled_bullet_level_from_family(family, active_section_profile, next_text=next_text)
        all_levels.append(current_level)
        node = {"level": current_level, "text": line, "children": []}
        while stack and int(stack[-1]["level"]) >= current_level:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)
        stack.append(node)
        last_node = node

    if not roots:
        return []

    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    max_rank = max(all_levels or [1])
    bullet_count = len(all_levels)
    fragment_level = section_context.get("body_fragment_level") if isinstance(section_context, dict) else None
    try:
        fragment_level = int(fragment_level) if fragment_level is not None else None
    except (TypeError, ValueError):
        fragment_level = None

    if intro_lines and max_rank != 2:
        if roots:
            roots[0]["text"] = "\n".join(intro_lines + [str(roots[0]["text"])])
        else:
            return []

    def emit_row(item_name: str, requirement: str, detail_requirement: str, split_long_detail: bool = False, prevent_split: bool = False) -> None:
        normalized_item = _normalize_requirement_text(item_name)
        normalized_req = _normalize_requirement_text(requirement)
        normalized_detail = _normalize_requirement_text(detail_requirement)
        if not normalized_item or not normalized_req or not normalized_detail:
            return
        normalized_detail = _normalize_requirement_text(normalized_detail)
        kv_match = re.match(
            r"^(?:[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+)\s*(?P<key>[^:\n]{1,40})\s*[:：]\s*(?P<value>.+)$",
            normalized_detail,
        )
        if kv_match and (
            normalized_req == normalized_item
            or _is_brief_heading_phrase(normalized_req)
            or _is_header_like_field(normalized_req)
        ):
            kv_key = _normalize_requirement_text(str(kv_match.group("key") or ""))
            kv_value = _normalize_requirement_text(str(kv_match.group("value") or ""))
            if kv_key and kv_value:
                normalized_req = kv_key
                normalized_detail = kv_value
        if prevent_split:
            detail_units = [normalized_detail]
        else:
            detail_units = (
                _split_body_detail_units_max_two_sentences(normalized_detail)
                if split_long_detail
                else (_split_atomic_detail_units(normalized_detail) or [normalized_detail])
            )
        for detail_unit in detail_units:
            if _is_only_page_info(detail_unit):
                continue
            key = (normalized_item, normalized_req, detail_unit)
            if key in seen:
                continue
            seen.add(key)
            if bullet_count <= 1:
                source_detail_hint = _format_body_source_detail_label(
                    item_name=normalized_item,
                    requirement=normalized_req,
                    detail_requirement=detail_unit,
                    bullet_count=bullet_count,
                    fragment_level=fragment_level,
                )
            elif bullet_count == 2:
                source_detail_hint = _format_body_source_detail_label(
                    item_name=normalized_item,
                    requirement=normalized_req,
                    detail_requirement=detail_unit,
                    bullet_count=bullet_count,
                    fragment_level=fragment_level,
                )
            elif bullet_count == 3:
                source_detail_hint = _format_body_source_detail_label(
                    item_name=normalized_item,
                    requirement=normalized_req,
                    detail_requirement=detail_unit,
                    bullet_count=bullet_count,
                    fragment_level=fragment_level,
                )
            elif bullet_count == 4:
                source_detail_hint = _format_body_source_detail_label(
                    item_name=normalized_item,
                    requirement=normalized_req,
                    detail_requirement=detail_unit,
                    bullet_count=bullet_count,
                    fragment_level=fragment_level,
                )
            else:
                source_detail_hint = _format_body_source_detail_label(
                    item_name=normalized_item,
                    requirement=normalized_req,
                    detail_requirement=detail_unit,
                    bullet_count=bullet_count,
                    fragment_level=fragment_level,
                )
            rows.append(
                {
                    "item_name": normalized_item,
                    "requirement": normalized_req,
                    "detail_requirement": detail_unit,
                    "result_note": "",
                    "build_method": "룰 기반(본문 계층 정규화)",
                    "build_source_detail": source_detail_hint,
                }
            )

    def walk(node: dict, lineage: list[str], detail_path: list[str] | None = None) -> None:
        rank = int(node["level"])
        text = _normalize_requirement_text(str(node["text"]))
        children = [child for child in list(node.get("children") or []) if _normalize_requirement_text(str(child.get("text") or ""))]

        if rank == 1:
            heading_text, intro_detail_lines = _split_body_heading_and_detail_lines(text)
            normalized_heading = _normalize_requirement_text(heading_text or text)
            normalized_intro_detail_lines = [
                _normalize_requirement_text(line)
                for line in intro_detail_lines
                if _normalize_requirement_text(line)
            ]
            intro_detail_text = "\n".join(normalized_intro_detail_lines)
            intro_starts_with_korean_bullet = bool(re.match(r"^[가나다라마바사아자차카타파하ㄱ-ㅎ][\.\)]\s+", normalized_intro_detail_lines[0])) if normalized_intro_detail_lines else False
            if not children:
                if normalized_intro_detail_lines:
                    if intro_starts_with_korean_bullet:
                        emit_row(
                            normalized_heading,
                            intro_detail_lines[0],
                            intro_detail_text,
                            split_long_detail=True,
                        )
                    else:
                        emit_row(
                            normalized_heading,
                            normalized_heading,
                            intro_detail_text,
                            split_long_detail=True,
                        )
                else:
                    emit_row(default_level1, normalized_title or default_level1, text)
                return
            if not normalized_intro_detail_lines and _is_title_like_requirement_text(normalized_heading) and normalized_heading == text:
                for child in children:
                    walk(child, [normalized_heading], None)
                return
            if normalized_intro_detail_lines:
                if intro_starts_with_korean_bullet:
                    emit_row(
                        normalized_heading,
                        intro_detail_lines[0],
                        intro_detail_text,
                        split_long_detail=True,
                    )
                else:
                    emit_row(
                        normalized_heading,
                        normalized_heading,
                        intro_detail_text,
                        split_long_detail=True,
                    )
            for child in children:
                walk(child, [normalized_heading], None)
            return

        if rank == 2:
            parent_item_name = lineage[0] if lineage else default_level1
            if not children:
                parent_requirement = lineage[-1] if lineage else (normalized_title or default_level1)
                emit_row(parent_item_name, parent_requirement, text)
                return
            for child in children:
                walk(child, [parent_item_name, text], None)
            return

        item_name = lineage[0] if lineage else default_level1
        requirement = lineage[1] if len(lineage) > 1 else text
        current_detail_path = (detail_path or []) + [text]
        if not children:
            merged_detail = "\n".join(current_detail_path)
            should_prevent = (max_rank > 3)
            emit_row(item_name, requirement, merged_detail, split_long_detail=False, prevent_split=should_prevent)
            return
        for child in children:
            walk(child, lineage, current_detail_path)

    if max_rank == 1:
        for root in roots:
            emit_row(default_level1, normalized_title or default_level1, _normalize_requirement_text(str(root["text"])))
        return _split_rows_with_inline_numbered_heading(rows)

    if max_rank == 2:
        pending_intro_details = [_normalize_requirement_text(line) for line in intro_lines if _normalize_requirement_text(line)]
        for root_idx, root in enumerate(roots):
            root_text = _normalize_requirement_text(str(root.get("text") or ""))
            children = [child for child in list(root.get("children") or []) if _normalize_requirement_text(str(child.get("text") or ""))]
            if not root_text:
                continue
            root_is_shared_parent = bool(children) and re.match(r"^[○●◦□Oo]\s+", root_text) is not None
            if root_is_shared_parent:
                parent_item_name, parent_intro_lines = _split_body_heading_and_detail_lines(root_text)
                parent_intro_text = "\n".join(
                    _normalize_requirement_text(line)
                    for line in parent_intro_lines
                    if _normalize_requirement_text(line)
                )
                shared_parent_context_text = "\n".join(
                    text
                    for text in (
                        "\n".join(pending_intro_details) if pending_intro_details else "",
                        parent_intro_text,
                    )
                    if text
                )
                parent_item_name = _normalize_requirement_text(root_text)
                if pending_intro_details:
                    joined_intro_details = "\n".join(pending_intro_details)
                    emit_row(default_level1, normalized_title or default_level1, joined_intro_details, split_long_detail=True)
                    pending_intro_details = []
                if parent_intro_lines:
                    intro_requirement_text, intro_detail_lines = _split_body_heading_and_detail_lines(parent_intro_text)
                    intro_requirement_text = _normalize_requirement_text(intro_requirement_text or parent_intro_text)
                    normalized_intro_detail_lines = [
                        _normalize_requirement_text(line)
                        for line in intro_detail_lines
                        if _normalize_requirement_text(line)
                    ]
                    if normalized_intro_detail_lines:
                        emit_row(
                            parent_item_name,
                            intro_requirement_text,
                            "\n".join(normalized_intro_detail_lines),
                            split_long_detail=True,
                        )
                    elif intro_requirement_text:
                        emit_row(parent_item_name, intro_requirement_text, intro_requirement_text)
                for child in children:
                    child_text = _normalize_requirement_text(str(child.get("text") or ""))
                    if not child_text:
                        continue
                    requirement_text, child_intro_detail_lines = _split_body_heading_and_detail_lines(child_text)
                    requirement_text = _normalize_requirement_text(requirement_text or child_text)
                    child_has_bullets = _has_descendant_bullets(child)
                    if child_intro_detail_lines:
                        joined_child_intro = "\n".join(
                            _normalize_requirement_text(line)
                            for line in child_intro_detail_lines
                            if _normalize_requirement_text(line)
                        )
                        if joined_child_intro and child_has_bullets:
                            emit_row(parent_item_name, requirement_text, joined_child_intro, split_long_detail=True)
                            continue
                    if child_has_bullets:
                        emit_row(parent_item_name, requirement_text, requirement_text)
                    else:
                        detail_text = joined_child_intro if child_intro_detail_lines else requirement_text
                        emit_row(parent_item_name, parent_item_name, detail_text, split_long_detail=bool(child_intro_detail_lines))
                continue
            requirement_text, intro_detail_lines = _split_body_heading_and_detail_lines(root_text)
            requirement_text = _normalize_requirement_text(requirement_text or root_text)
            prelude_details = list(pending_intro_details)
            pending_intro_details = []
            inline_intro_details = [
                _normalize_requirement_text(line)
                for line in intro_detail_lines
                if _normalize_requirement_text(line)
            ]
            if prelude_details:
                emit_row(
                    default_level1,
                    normalized_title or default_level1,
                    "\n".join(prelude_details),
                    split_long_detail=True,
                )
            if not children:
                detail_text = "\n".join(inline_intro_details) if inline_intro_details else requirement_text
                emit_row(
                    default_level1,
                    default_level1,
                    detail_text,
                    split_long_detail=bool(inline_intro_details),
                )
                continue
            for child in children:
                child_text = _normalize_requirement_text(str(child.get("text") or ""))
                if not child_text:
                    continue
                emit_row(default_level1, requirement_text, child_text, split_long_detail=True)
        return _split_rows_with_inline_numbered_heading(rows)

    for root in roots:
        walk(root, [])
    return _split_rows_with_inline_numbered_heading(rows)


def _extract_shared_parent_lettered_body_rows(
    plain_text: str,
    title: str = "",
    section_context: dict | None = None,
) -> list[dict]:
    normalized = _normalize_requirement_text(plain_text)
    if not normalized:
        return []
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return []
    default_level1 = _normalize_requirement_text((section_context or {}).get("default_item_name") or title or lines[0])
    return _extract_atomic_body_rows(
        lines,
        title=title,
        default_item_name=default_level1,
        build_method="룰 기반(단일 행 본문)",
    )


def _section_requirement_prefix(section_name: str) -> str:
    compact = re.sub(r"\s+", " ", (section_name or "")).strip()
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇\(\)\[\]]+\s*", "", compact)
    compact = re.sub(r"^(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+[\.\)]?)\s*", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"[\s/]+", "", compact)
    compact = re.sub(r"[^0-9A-Za-z가-힣]+", "", compact)
    return (compact or "요구사항")[:12]


def _clean_item_name_for_id(item_name: str) -> str:
    cleaned = _normalize_requirement_text(item_name)
    cleaned = re.sub(r"^[○●◦•□▪■◆▶]+\s*", "", cleaned).strip()
    cleaned = re.sub(r"^[Oo]\s+(?=\S)", "", cleaned).strip()
    cleaned = re.sub(
        r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"\b(?:등|등의|등을|등은|등에|등으로|등과 같은)\b.*$", "", cleaned).strip()
    cleaned = re.sub(r"(?:입니다|한다|해야 함|하여야 함|필요|지원|제공)$", "", cleaned).strip()
    return cleaned


def _extract_domain_prefix(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    for token in ["UX/UI", "UI/UX", "UX", "UI", "RAG", "ICT", "AI", "MCP"]:
        if token in normalized:
            return token
    paren_match = re.search(r"\(([^()]{1,20})\)", normalized)
    if paren_match:
        inner = _normalize_requirement_text(paren_match.group(1))
        inner = re.sub(r"\s+", "", inner)
        if inner:
            return inner
    return ""


def _looks_like_broad_requirement_group_label(text: str, category: str = "", section_name: str = "") -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return True
    normalized_category = _normalize_requirement_text(category)
    normalized_section = _normalize_requirement_text(section_name)
    if normalized in {normalized_category, normalized_section}:
        return True
    if re.match(r"^\s*(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s*", normalized):
        return True
    broad_tokens = [
        "제안",
        "제안요청 개요",
        "상세 요구사항",
        "상세요구사항",
        "요구사항",
        "제안 요구사항",
        "구축 사업",
        "제안의 평가",
        "정보분석계",
        "프로젝트 범위",
        "사업 개요",
    ]
    return any(token in normalized for token in broad_tokens)


def _normalize_table_item_name_with_card_title(
    item_name: str,
    *,
    card_title: str,
    default_item_name: str = "",
    card_requirement: str = "",
    section_title: str = "",
    part_text: str = "",
) -> str:
    normalized_item = _normalize_requirement_text(item_name)
    normalized_card_title = _normalize_requirement_text(card_title)
    normalized_default = _normalize_requirement_text(default_item_name)
    normalized_requirement = _normalize_requirement_text(card_requirement)
    normalized_section = _normalize_requirement_text(section_title)
    normalized_part = _normalize_requirement_text(part_text)

    fallback_title = normalized_default or normalized_card_title
    if not fallback_title:
        return normalized_item

    broad_candidates = {
        value
        for value in (
            normalized_requirement,
            normalized_section,
            normalized_part,
        )
        if value
    }
    if not normalized_item:
        return fallback_title
    if normalized_item in broad_candidates and normalized_item != fallback_title:
        return fallback_title
    if _looks_like_broad_requirement_group_label(normalized_item, normalized_requirement, normalized_section):
        if fallback_title and fallback_title != normalized_item:
            return fallback_title
    return normalized_item


def _select_requirement_id_source(
    item_name: str,
    requirement: str,
    category: str,
    section_name: str,
    id_prefix_hint: str = "",
    id_title_hint: str = "",
) -> str:
    explicit_hint = _normalize_requirement_text(id_prefix_hint or id_title_hint)
    if explicit_hint:
        return explicit_hint

    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)

    if normalized_item == "공통":
        return normalized_item

    item_broad = _looks_like_broad_requirement_group_label(normalized_item, category, section_name)
    requirement_broad = _looks_like_broad_requirement_group_label(normalized_requirement, category, section_name)

    # If item_name is a broad section/card label and requirement is the actual subgroup,
    # use requirement as the grouping source so rows under the same subgroup share one ID family.
    if item_broad and normalized_requirement and not requirement_broad:
        return normalized_requirement

    # If item_name and requirement are identical, keep one representative source.
    if normalized_item and normalized_item == normalized_requirement:
        return normalized_item

    if normalized_item and not item_broad:
        return normalized_item
    if normalized_requirement:
        return normalized_requirement
    return normalized_item or normalized_requirement or _normalize_requirement_text(category) or _normalize_requirement_text(section_name)


def _row_requirement_id_prefix(item_name: str, category: str, section_name: str, fallback_prefix: str) -> str:
    cleaned_item_name = _clean_item_name_for_id(item_name)
    if not cleaned_item_name:
        return fallback_prefix

    if cleaned_item_name == "공통":
        domain = _extract_domain_prefix(category) or _extract_domain_prefix(section_name)
        if domain:
            return f"{domain}공통"
        return "공통"

    domain = _extract_domain_prefix(cleaned_item_name)
    if domain and domain != cleaned_item_name:
        return domain

    # 문장 수준으로 길 경우 핵심 단어들만 룰 기반으로 추출하여 ID 프리픽스 가독성 및 정제 효율 개선
    words = [w for w in cleaned_item_name.split() if w.strip()]
    if len(words) >= 2:
        filter_words = []
        for w in words[:2]:
            cleaned_w = re.sub(r"(?:관련|위한|대한|구축|개발|수행|요구사항|기준|대상|내용|제공|지원|시스템)$", "", w)
            if cleaned_w:
                filter_words.append(cleaned_w)
        if filter_words:
            compact = "".join(filter_words)
        else:
            compact = "".join(words[:2])
    else:
        compact = cleaned_item_name

    compact = re.sub(r"[^0-9A-Za-z가-힣/\s]+", "", compact).strip()
    compact = re.sub(r"\s+", " ", compact)

    # Do not cut through a word boundary for Latin-word labels such as
    # "Object Storage" -> "ObjectStorage", not "ObjectSt".
    word_tokens = [token for token in compact.split(" ") if token]
    if len(word_tokens) >= 2:
        joined = "".join(word_tokens)
        if len(joined) <= 20:
            return joined
        kept: list[str] = []
        current_len = 0
        for token in word_tokens:
            token_len = len(token)
            if kept and current_len + token_len > 20:
                break
            if not kept and token_len > 20:
                return token[:20]
            kept.append(token)
            current_len += token_len
        if kept:
            return "".join(kept)

    compact_no_space = re.sub(r"\s+", "", compact)
    return (compact_no_space or fallback_prefix)[:20]


def _needs_llm_requirement_id_prefix(item_name: str) -> bool:
    normalized = _normalize_requirement_text(item_name)
    cleaned = _clean_item_name_for_id(normalized)
    if not cleaned:
        return False

    # 공통, UX/UI, AI 같은 짧고 명확한 라벨은 룰 기반으로 충분합니다.
    if len(cleaned) <= 6 and " " not in cleaned and re.fullmatch(r"[0-9A-Za-z가-힣/]+", cleaned):
        return False
    if (
        len(normalized.split()) <= 2
        and re.fullmatch(r"[0-9A-Za-z가-힣/\-\+\(\)\s]+", normalized)
        and not re.search(r"\d+\.", normalized)
        and not any(token in normalized for token in ["요구사항", "제안요청", "설명", "제시", "필요", "가능", "하여야", "해야"])
    ):
        return False

    # 번호가 붙은 섹션 제목, 문장형 문구, 또는 지나치게 긴 라벨은 LLM으로 짧은 명사형을 추론합니다.
    if re.match(r"^\s*(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s*", normalized):
        return True
    if any(token in normalized for token in [":", "：", ",", "，", "·", "∙", "→", ">", " 및 ", " 또는 ", "으로 ", "하여 ", "해야", "하여야", "필요", "가능", "설명", "제시"]):
        return True
    if len(cleaned) >= 10:
        return True
    if len(cleaned.split()) >= 3:
        return True
    if any(token in normalized for token in ["요구사항", "제안요청", "상세 요구사항", "상세요구사항", "구축 사업", "제안요청 개요"]):
        return True
    return False


def _infer_requirement_id_prefix_via_llm(client, item_name: str, category: str, section_name: str, fallback_prefix: str, model_name: str) -> tuple[str, dict]:
    payload = {
        "item_name": _normalize_requirement_text(item_name),
        "category": _normalize_requirement_text(category),
        "section_name": _normalize_requirement_text(section_name),
        "fallback_prefix": _normalize_requirement_text(fallback_prefix),
    }
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You create one short requirement ID prefix for one RFP item. "
                    "Return JSON object only with key: id_prefix. "
                    "The prefix must be a short Korean noun-like label, not a sentence. "
                    "Prefer 2 to 8 Hangul characters when possible. "
                    "Do not include trailing particles or phrases such as '등', '관련', '위한', '대한', '제공', '지원'. "
                    "Do not include numbering or bullet markers. "
                    "If item_name is a sentence, compress it into the most specific noun phrase. "
                    "Good examples: '온프레미스', '정보처리시스템', '서비스설계', '프로젝트관리', '그래픽디자인'."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        **_responses_create_kwargs(model_name),
    )
    raw_content = (getattr(response, "output_text", None) or "").strip()
    parsed = _extract_json_payload(raw_content)
    id_prefix = ""
    if isinstance(parsed, dict):
        id_prefix = _normalize_requirement_text(str(parsed.get("id_prefix") or "").strip())
    id_prefix = _clean_item_name_for_id(id_prefix)
    id_prefix = re.sub(r"\s+", "", id_prefix)
    id_prefix = re.sub(r"[^0-9A-Za-z가-힣/]+", "", id_prefix)
    input_tokens, output_tokens = _extract_token_usage(response)
    usage = {
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, input_tokens, output_tokens),
    }
    return (id_prefix or fallback_prefix), usage


def _safe_sheet_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[\[\]\*\?/\\:]", " ", (value or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or fallback)[:31]


def _section_rows_to_xlsx_bytes(section_rows: dict[str, list[dict]]) -> bytes:
    def col_name(idx: int) -> str:
        name = ""
        while idx:
            idx, rem = divmod(idx - 1, 26)
            name = chr(65 + rem) + name
        return name

    def xml_escape(value: object) -> str:
        text = "" if value is None else str(value)
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    worksheet_files: dict[str, str] = {}
    workbook_sheets: list[str] = []
    rels_entries: list[str] = []
    for idx, (sheet_name, rows) in enumerate(section_rows.items(), start=1):
        safe_name = _safe_sheet_name(sheet_name, f"Section{idx}")
        workbook_sheets.append(f'<sheet name="{xml_escape(safe_name)}" sheetId="{idx}" r:id="rId{idx}"/>')
        rels_entries.append(
            f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        )
        headers = list(rows[0].keys()) if rows else []
        all_rows = [headers] + [[row.get(h, "") for h in headers] for row in rows]
        xml_rows: list[str] = []
        for r_idx, row in enumerate(all_rows, start=1):
            cells: list[str] = []
            for c_idx, value in enumerate(row, start=1):
                ref = f"{col_name(c_idx)}{r_idx}"
                style = ' s="1"' if r_idx == 1 else ""
                cells.append(
                    f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{xml_escape(value)}</t></is></c>'
                )
            xml_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
        worksheet_files[f"xl/worksheets/sheet{idx}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheetData>" + "".join(xml_rows) + "</sheetData></worksheet>"
        )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>'
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(section_rows) + 1)
            )
            + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>'
        ))
        zf.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'
        ))
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels_entries)
            + '<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>'
        ))
        for path, xml in worksheet_files.items():
            zf.writestr(path, xml)
        zf.writestr("xl/styles.xml", styles_xml)
    return buffer.getvalue()


def _group_cards_by_section(cards: list[RfpCard]) -> list[tuple[str, list[RfpCard]]]:
    grouped: dict[str, list[RfpCard]] = {}
    order: list[str] = []
    for card in cards:
        group_name = str(getattr(card, "part", "") or "").strip()
        section_name = str(getattr(card, "section", "") or "").strip()
        requirement_name = str(getattr(card, "requirement", "") or "").strip()
        if group_name and section_name and group_name != section_name:
            section_label = f"{section_name} < {group_name}"
        else:
            section_label = section_name or group_name or requirement_name or "기타"
        if section_label not in grouped:
            grouped[section_label] = []
            order.append(section_label)
        grouped[section_label].append(card)
    return [(name, grouped[name]) for name in order]


def _summarize_table_card_rule_debug(card: RfpCard, section_context: dict | None = None) -> list[dict]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if "<table" not in html_excerpt.lower():
        return []

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    summaries: list[dict] = []
    section_context = section_context or {}
    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    leading_context = _leading_body_context_from_table_html(html_excerpt, card_title)
    preferred_item_context = str(leading_context or section_context.get("default_item_name") or section_context.get("section_title") or card_title or "").strip()

    for idx, table in enumerate(tables, start=1):
        matrix = _table_visual_matrix(table, preserve_breaks=True)
        if not matrix:
            summaries.append(
                {
                    "table_index": idx,
                    "branch": "empty_matrix",
                    "max_cols": 0,
                    "has_header_row": False,
                    "header_row": [],
                }
            )
            continue

        max_cols_before_drop = max(len(row) for row in matrix)
        dropped_numbering_col = None
        if max_cols_before_drop == 4:
            normalized_matrix = matrix
            if max_cols_before_drop != 3:
                normalized_matrix, dropped_numbering_col = _drop_numbering_column_from_matrix(matrix)
            matrix = normalized_matrix

        max_cols = max(len(row) for row in matrix)
        first_tr = table.find("tr")
        has_header_row = bool(first_tr and first_tr.find("th"))
        header_row = [str(cell or "").strip() for cell in matrix[0]] if matrix else []
        numeric_aux_third = max_cols == 3 and _is_numeric_auxiliary_third_column(matrix)
        is_two_level_three_col = max_cols == 3 and _is_auxiliary_third_column_header(header_row) or numeric_aux_third
        is_three_level_four_col_with_aux_last = max_cols == 4 and _is_auxiliary_fourth_column_header(header_row)
        is_grouped_three_col = max_cols == 3 and _is_grouped_three_level_header(header_row)
        is_box_style_three_column = max_cols == 3 and _is_box_style_three_column_table(matrix)
        is_single_row_header_continuation = _is_single_row_header_continuation_table(matrix, has_header_row)
        if is_single_row_header_continuation:
            has_header_row = False

        if is_box_style_three_column:
            branch = "3단표-박스형본문"
        elif max_cols == 2:
            branch = "2단표"
        elif is_two_level_three_col:
            branch = "3단표->2단표+비고/추가정보"
        elif is_three_level_four_col_with_aux_last:
            branch = "4단표->3단표+추가정보"
        elif is_grouped_three_col:
            branch = "3단표-그룹헤더"
        elif dropped_numbering_col is not None and max_cols == 3:
            branch = "4단표-번호컬럼제거후3단처리"
        elif max_cols == 3 and len(matrix) == 1 and not has_header_row:
            branch = "3단표-단일행fallback"
        elif max_cols == 3:
            branch = "일반3단표"
        else:
            branch = f"{max_cols}단표"

        summaries.append(
            {
                "table_index": idx,
                "branch": branch,
                "max_cols": max_cols,
                "has_header_row": has_header_row,
                "header_row": header_row,
                "dropped_numbering_col": dropped_numbering_col,
                "leading_context": leading_context,
                "preferred_item_context": preferred_item_context,
                "table_preview": _normalize_requirement_text(" ".join(_normalize_requirement_text(str(cell or "")) for row in matrix[:2] for cell in row))[:300],
            }
        )

    return summaries


def _extract_rows_from_table_card(
    card: RfpCard,
    two_col_item_name_override: str = "",
    section_context: dict | None = None,
) -> list[dict]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if "<table" not in html_excerpt.lower():
        return []

    def _extract_leading_body_rows_before_first_table() -> list[dict]:
        blocks = _body_text_blocks(html_excerpt)
        if not blocks:
            return []
        leading_blocks: list[dict] = []
        for block in blocks:
            if block.get("tag") == "table":
                break
            leading_blocks.append(block)
        if not leading_blocks:
            return []
        leading_html = "\n".join(str(block.get("html") or "") for block in leading_blocks).strip()
        if not leading_html:
            return []
        leading_plain = _plain_text_from_html_excerpt(leading_html)
        if not leading_plain:
            return []
        default_item_name = str(getattr(card, "subject", None) or card.requirement or "").strip()
        hierarchical_rows = _extract_hierarchical_body_rows(
            leading_plain,
            title=card_title,
            default_item_name=default_item_name,
            section_context=section_context,
        )
        shared_parent_rows = _extract_shared_parent_lettered_body_rows(
            leading_plain,
            title=card_title,
            section_context=section_context,
        )
        body_rows = shared_parent_rows if len(shared_parent_rows) >= 2 else hierarchical_rows
        normalized_rows: list[dict] = []
        for row in body_rows:
            item_name = _normalize_requirement_text(str(row.get("item_name") or ""))
            requirement = _normalize_requirement_text(str(row.get("requirement") or ""))
            detail_requirement = _normalize_requirement_text(str(row.get("detail_requirement") or ""))
            result_note = _normalize_requirement_text(str(row.get("result_note") or ""))
            if not item_name or not requirement or not detail_requirement:
                continue
            normalized_rows.append(
                {
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": detail_requirement,
                    "result_note": result_note,
                    "id_title_hint": "",
                    "build_method": "룰 기반(표전 본문)",
                }
            )
        if len(normalized_rows) == 1:
            only_row = normalized_rows[0]
            only_item_name = _normalize_requirement_text(str(only_row.get("item_name") or ""))
            only_requirement = _normalize_requirement_text(str(only_row.get("requirement") or ""))
            only_detail = _normalize_requirement_text(str(only_row.get("detail_requirement") or ""))
            if (
                only_item_name
                and only_item_name == only_requirement == only_detail
                and _title_match(card_title, only_item_name)
            ):
                return []
        return normalized_rows

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    extracted_rows: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    section_context = section_context or {}
    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    leading_context = _leading_body_context_from_table_html(html_excerpt, card_title)
    preferred_item_context = str(
        leading_context
        or section_context.get("default_item_name")
        or section_context.get("section_title")
        or card_title
        or ""
    )
    inferred_item_name_context = ""
    inferred_requirement_context = ""

    for row in _extract_leading_body_rows_before_first_table():
        unit_key = (
            _normalize_requirement_text(str(row.get("result_note") or "")),
            _normalize_requirement_text(str(row.get("item_name") or "")),
            _normalize_requirement_text(str(row.get("requirement") or "")),
            _normalize_requirement_text(str(row.get("detail_requirement") or "")),
        )
        if unit_key in seen:
            continue
        seen.add(unit_key)
        extracted_rows.append(row)

    for idx, table in enumerate(tables, start=1):
        matrix = _table_visual_matrix(table, preserve_breaks=True)
        if not matrix:
            continue
        top_table_context, top_context_skip_rows = _top_table_context_from_matrix(matrix, card_title)
        preferred_item_context = str(
            leading_context
            or top_table_context
            or section_context.get("default_item_name")
            or section_context.get("section_title")
            or card_title
            or ""
        )
        max_cols_before_drop = max(len(row) for row in matrix)
        dropped_numbering_col = None
        numbering_source_matrix: list[list[str]] | None = None
        # Some broken HTML tables are really 3-column tables but arrive as 4-column
        # matrices because repeated header rows inherit a dangling rowspan value in col 0.
        # Normalize those back to a clean 3-column matrix before rule matching.
        if max_cols_before_drop == 4 and matrix:
            header3 = [_normalize_requirement_text(str(cell or "")) for cell in matrix[0][:3]]
            if header3 and all(header3):
                normalized_matrix: list[list[str]] = []
                converted = False
                for row in matrix:
                    padded = list(row) + [""] * max(0, 4 - len(row))
                    norm = [_normalize_requirement_text(str(cell or "")) for cell in padded[:4]]
                    if norm[1:] == header3:
                        normalized_matrix.append(list(header3))
                        converted = True
                        continue
                    if not norm[3]:
                        normalized_matrix.append(padded[:3])
                        converted = True
                        continue
                    normalized_matrix.append(padded[:4])
                if converted and all(len(row) == 3 for row in normalized_matrix):
                    matrix = normalized_matrix
                    max_cols_before_drop = 3
        if max_cols_before_drop in {4, 5}:
            numbering_source_matrix = [list(row) for row in matrix]
            matrix, dropped_numbering_col = _drop_numbering_column_from_matrix(matrix)
        max_cols = max(len(row) for row in matrix)
        if max_cols == 1:
            single_col_rows = _extract_single_column_table_rows(
                card,
                matrix,
                card_title,
                leading_context or preferred_item_context,
                has_header_row=bool(table.find("tr") and table.find("tr").find("th")),
            )
            for row in single_col_rows:
                unit_key = (
                    _normalize_requirement_text(str(row.get("result_note") or "")),
                    _normalize_requirement_text(str(row.get("item_name") or "")),
                    _normalize_requirement_text(str(row.get("requirement") or "")),
                    _normalize_requirement_text(str(row.get("detail_requirement") or "")),
                )
                if unit_key in seen:
                    continue
                seen.add(unit_key)
                extracted_rows.append(row)
            continue
        if max_cols not in {2, 3, 4}:
            continue

        if max_cols == 3 and _is_box_style_three_column_table(matrix):
            leading_text = _normalize_requirement_text(leading_context or preferred_item_context or card_title or card.requirement or getattr(card, "subject", None) or "")
            if not leading_text:
                leading_text = _normalize_requirement_text(str(getattr(card, "subject", None) or card.requirement or ""))
            for row in matrix:
                padded = list(row) + [""] * max(0, 3 - len(row))
                detail_requirement = _normalize_requirement_text(str(padded[2] or ""))
                if not detail_requirement:
                    continue
                detail_units = _split_table_cell_lines(detail_requirement)
                if len(detail_units) <= 1:
                    detail_units = _split_atomic_detail_units(detail_requirement) or [detail_requirement]
                for detail_unit in detail_units:
                    cleaned_detail = _strip_trailing_orphan_bullet(_normalize_requirement_text(detail_unit))
                    if not cleaned_detail or _is_only_page_info(cleaned_detail):
                        continue
                    unit_key = ("", leading_text, leading_text, cleaned_detail)
                    if unit_key in seen:
                        continue
                    seen.add(unit_key)
                    extracted_rows.append(
                        {
                            "item_name": leading_text,
                            "requirement": leading_text,
                            "detail_requirement": cleaned_detail,
                            "result_note": "",
                            "id_title_hint": "",
                            "build_method": "룰 기반(3단 표-박스형 본문)",
                            "table_rule_branch": "3단표-박스형본문",
                            "table_index": idx,
                        }
                    )
            continue

        first_tr = table.find("tr")
        has_header_row = bool(first_tr and first_tr.find("th"))
        is_single_row_header_continuation = _is_single_row_header_continuation_table(matrix, has_header_row)
        if is_single_row_header_continuation:
            has_header_row = False
        header_row = [str(cell or "").strip() for cell in matrix[0]]
        numeric_aux_third = max_cols == 3 and _is_numeric_auxiliary_third_column(matrix)
        is_two_level_three_col = max_cols == 3 and (
            _is_auxiliary_third_column_header(header_row) or numeric_aux_third
        )
        is_three_level_four_col_with_aux_last = max_cols == 4 and _is_auxiliary_fourth_column_header(header_row)
        is_grouped_three_col = max_cols == 3 and _is_grouped_three_level_header(header_row)
        embedded_first_data_row: list[str] | None = None
        is_box_like_single_row_three_col = False
        if has_header_row and max_cols == 3:
            split_cells: list[tuple[str, str]] = []
            for cell in header_row:
                parts = [part.strip() for part in re.split(r"\n\s*\n|\n", str(cell or "").strip()) if part.strip()]
                head = parts[0] if parts else ""
                tail = "\n\n".join(parts[1:]).strip() if len(parts) > 1 else ""
                split_cells.append((head, tail))
            normalized_heads = [_normalize_requirement_text(head) for head, _ in split_cells]
            if (
                _is_auxiliary_third_column_header(normalized_heads)
                and sum(1 for _, tail in split_cells if _normalize_requirement_text(tail)) >= 2
            ):
                header_row = [head for head, _ in split_cells]
                matrix[0] = header_row
                embedded_first_data_row = [tail for _, tail in split_cells]

        data_rows = matrix[1:] if has_header_row and len(matrix) > 1 else matrix
        numbering_data_rows: list[list[str]] = []
        if numbering_source_matrix is not None:
            numbering_data_rows = (
                numbering_source_matrix[1:]
                if has_header_row and len(numbering_source_matrix) > 1
                else numbering_source_matrix
            )
        if embedded_first_data_row is not None:
            data_rows = [embedded_first_data_row, *data_rows]
        if max_cols == 2 and top_table_context and top_context_skip_rows:
            skip_count = max(top_context_skip_rows - (1 if has_header_row else 0), 0)
            if skip_count:
                data_rows = data_rows[skip_count:]

        table_rule_branch = (
            "3단표-박스형본문"
            if max_cols == 3 and _is_box_style_three_column_table(matrix)
            else "3단표->2단표+비고/추가정보"
            if is_two_level_three_col
            else "4단표->3단표+추가정보"
            if is_three_level_four_col_with_aux_last
            else "3단표-그룹헤더"
            if is_grouped_three_col
            else "4단표-번호컬럼제거후3단처리"
            if dropped_numbering_col is not None and max_cols == 3
            else "3단표-단일행fallback"
            if max_cols == 3 and len(matrix) == 1 and not has_header_row
            else "일반3단표"
            if max_cols == 3
            else ("2단표" if max_cols == 2 else f"{max_cols}단표")
        )

        for row_idx, row in enumerate(data_rows):
            padded = list(row) + [""] * max(0, max_cols - len(row))
            original_row_signature = [str(cell or "").strip() for cell in padded[:max_cols]]
            if has_header_row and original_row_signature == header_row:
                continue
            numbered_row = any(_has_leading_numbering(str(cell or "")) for cell in padded[:max_cols])
            result_note = ""
            dropped_numbering_value = ""
            empty_leading_two_cols_three_col = (
                max_cols == 3
                and not has_header_row
                and not str(padded[0] or "").strip()
                and not str(padded[1] or "").strip()
                and str(padded[2] or "").strip()
            )
            if (
                dropped_numbering_col is not None
                and numbering_data_rows
                and row_idx < len(numbering_data_rows)
                and dropped_numbering_col < len(numbering_data_rows[row_idx])
            ):
                dropped_numbering_value = _normalize_requirement_text(
                    str(numbering_data_rows[row_idx][dropped_numbering_col] or "")
                )
            if max_cols == 2:
                item_name = _normalize_requirement_text(str(two_col_item_name_override or "")) or _normalize_two_col_table_item_name(
                    preferred_item_context,
                    card_title,
                )
                requirement = _normalize_requirement_text(str(padded[0] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[1] or ""))
            elif is_two_level_three_col:
                item_name = _normalize_requirement_text(str(two_col_item_name_override or "")) or _normalize_two_col_table_item_name(
                    preferred_item_context,
                    card_title,
                )
                requirement = _normalize_requirement_text(str(padded[0] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[1] or ""))
                result_note = _normalize_requirement_text(str(padded[2] or ""))
            elif max_cols == 3:
                if empty_leading_two_cols_three_col:
                    is_box_like_single_row_three_col = True
                    item_name = _normalize_requirement_text(leading_context or preferred_item_context or card_title or card.requirement or getattr(card, "subject", None) or "")
                    requirement = item_name
                    detail_requirement = _normalize_requirement_text(str(padded[2] or ""))
                elif len(matrix) == 1 and not has_header_row and leading_context:
                    item_name = _normalize_requirement_text(leading_context)
                else:
                    item_name = _normalize_three_col_table_item_name(
                        str(padded[0] or ""),
                        preferred_item_context,
                        card_title,
                    )
                if not is_box_like_single_row_three_col:
                    requirement = _normalize_requirement_text(str(padded[1] or ""))
                    detail_requirement = _normalize_requirement_text(str(padded[2] or ""))
            elif is_three_level_four_col_with_aux_last:
                if len(matrix) == 1 and not has_header_row and leading_context:
                    item_name = _normalize_requirement_text(leading_context)
                else:
                    item_name = _normalize_three_col_table_item_name(
                        str(padded[0] or ""),
                        preferred_item_context,
                        card_title,
                    )
                requirement = _normalize_requirement_text(str(padded[1] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[2] or ""))
                result_note = _normalize_requirement_text(str(padded[3] or ""))
            else:
                result_note = _normalize_requirement_text(str(padded[0] or ""))
                item_name = _normalize_requirement_text(str(padded[1] or ""))
                requirement = _normalize_requirement_text(str(padded[2] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[3] or ""))

            if dropped_numbering_value:
                result_note = dropped_numbering_value

            if (
                _is_header_like_field(item_name)
                and _is_header_like_field(requirement)
                and detail_requirement
                and not _is_header_like_field(detail_requirement)
                and len(detail_requirement) <= 80
            ):
                inferred_item_name_context = detail_requirement
            if requirement and len(requirement) <= 40:
                inferred_requirement_context = requirement

            if not item_name and not requirement and not detail_requirement:
                continue
            row_signature = (
                [requirement, detail_requirement, result_note]
                if is_two_level_three_col
                else ([item_name, requirement, detail_requirement] if max_cols == 3
                else ([result_note, item_name, requirement, detail_requirement] if max_cols == 4 else [requirement, detail_requirement])
                )
            )
            if has_header_row and row_signature == header_row:
                continue
            if not detail_requirement:
                continue
            if not requirement:
                if is_single_row_header_continuation and inferred_requirement_context:
                    requirement = inferred_requirement_context
                else:
                    requirement = str(getattr(card, "subject", None) or card.requirement or "").strip()
            if not item_name and is_two_level_three_col:
                item_name = _normalize_two_col_table_item_name(
                    preferred_item_context,
                    card_title,
                )
            if not item_name and max_cols == 3:
                item_name = str(getattr(card, "subject", None) or card.requirement or getattr(card, "sub_subject", None) or "").strip()
            if not item_name and max_cols == 4:
                item_name = str(leading_context or getattr(card, "subject", None) or card.requirement or getattr(card, "sub_subject", None) or "").strip()
            if inferred_item_name_context and _is_noisy_table_item_name(item_name):
                item_name = inferred_item_name_context

            if is_box_like_single_row_three_col:
                if not item_name:
                    item_name = _normalize_requirement_text(leading_context or preferred_item_context or card_title or card.requirement or getattr(card, "subject", None) or "")
                if not requirement:
                    requirement = item_name or _normalize_requirement_text(card.requirement or card_title or "")
                if not detail_requirement:
                    detail_requirement = _normalize_requirement_text(str(padded[2] or ""))

            detail_units = _split_table_cell_lines(detail_requirement)
            if len(detail_units) <= 1:
                if max_cols == 3 or is_two_level_three_col:
                    detail_units = _split_three_col_detail_units(detail_requirement)
                else:
                    detail_units = _split_atomic_detail_units(detail_requirement)
            for detail_unit in detail_units or [detail_requirement]:
                unit_key = (result_note, item_name, requirement, detail_unit)
                if unit_key in seen:
                    continue
                seen.add(unit_key)
                id_title_hint = ""
                if (
                    max_cols == 3
                    and leading_context
                    and leading_context != card_title
                    and _is_promotable_leading_table_heading(leading_context, card_title)
                ):
                    id_title_hint = leading_context
                extracted_rows.append(
                    {
                        "item_name": item_name,
                        "requirement": requirement,
                        "detail_requirement": detail_unit,
                        "result_note": result_note,
                        "id_title_hint": id_title_hint,
                        "table_index": idx,
                        "table_rule_branch": table_rule_branch,
                        "build_method": (
                            "룰 기반(3단 표-박스형 본문)"
                            if is_box_like_single_row_three_col
                            else "룰 기반(4단 표->3단 표+추가정보)"
                            if is_three_level_four_col_with_aux_last
                            else "룰 기반(4단 표)"
                            if max_cols == 4
                            else "룰 기반(3단 표->2단 표+추가정보)"
                            if is_two_level_three_col
                            else "룰 기반(3단 표-그룹헤더)"
                            if is_grouped_three_col
                            else "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)"
                            if dropped_numbering_col is not None and max_cols == 3
                            else "룰 기반(넘버링 표->3단 처리)"
                            if max_cols == 2 and numbered_row
                            else ("룰 기반(2단 표)" if max_cols == 2 else "룰 기반(3단 표)")
                        ),
                    }
                )

        if (
            max_cols == 3
            and len(matrix) == 1
            and not extracted_rows
        ):
            padded = list(matrix[0]) + [""] * max(0, 3 - len(matrix[0]))
            fallback_item_name = _normalize_requirement_text(str(leading_context or preferred_item_context or card_title or getattr(card, "subject", None) or card.requirement or ""))
            fallback_requirement = _normalize_requirement_text(str(padded[0] or "")) or _normalize_requirement_text(str(card_title or getattr(card, "subject", None) or card.requirement or ""))
            fallback_detail = _normalize_requirement_text(str(padded[2] or ""))
            if fallback_item_name and fallback_requirement and fallback_detail:
                fallback_rows = _split_three_col_detail_units(fallback_detail) or [fallback_detail]
                for fallback_detail_unit in fallback_rows:
                    fallback_detail_unit = _normalize_requirement_text(fallback_detail_unit)
                    if not fallback_detail_unit:
                        continue
                    unit_key = ("", fallback_item_name, fallback_requirement, fallback_detail_unit)
                    if unit_key in seen:
                        continue
                    seen.add(unit_key)
                    extracted_rows.append(
                        {
                            "item_name": fallback_item_name,
                            "requirement": fallback_requirement,
                            "detail_requirement": fallback_detail_unit,
                            "result_note": "",
                            "id_title_hint": "",
                            "build_method": "룰 기반(3단 표-단일행 fallback)",
                            "table_rule_branch": "3단표-단일행fallback",
                            "table_index": idx,
                        }
                    )

    return extracted_rows


def _should_route_table_card_to_llm(card: RfpCard) -> bool:
    html_excerpt = str(card.html_excerpt or "").strip()
    if "<table" not in html_excerpt.lower():
        return False

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    if not tables:
        return False

    if any(_table_has_nested_table(table) for table in tables):
        return True

    saw_supported_table = False
    saw_unsupported_table = False
    for table in tables:
        matrix = _table_visual_matrix(table, preserve_breaks=True)
        if not matrix:
            continue
        max_cols = max(len(row) for row in matrix)
        if max_cols in {2, 3, 4}:
            saw_supported_table = True
            continue
        saw_unsupported_table = True
        if max_cols > 4:
            return True

    # If a single card mixes rule-supported tables with unsupported wide tables,
    # partial rule extraction tends to drop the complex table content. In that
    # case, let LLM see the whole card structure instead of accepting a partial hit.
    return saw_supported_table and saw_unsupported_table


def _section_context_fallback(section_name: str, cards: list[RfpCard]) -> dict:
    first_card = cards[0] if cards else None
    body_hierarchy_profile = _infer_section_body_hierarchy_profile(section_name, cards)
    hierarchy_sequence = body_hierarchy_profile.get("family_sequence") or []
    sequence_text = " > ".join(
        {
            "heading": "숫자/장절",
            "hangul_syllable": "가.나.다.",
            "dash_mid": "-(중간)",
            "hangul_jamo": "ㄱ.ㄴ.ㄷ.",
            "digit_dot": "1.",
            "digit_paren": "1)",
            "latin": "a.",
            "symbol": "•/○/O",
            "dash_low": "-(하위)",
            "other": "기타",
        }.get(family, family)
        for family in hierarchy_sequence
    )
    return {
        "section_title": section_name,
        "id_prefix": _section_requirement_prefix(section_name),
        "default_item_name": str(
            getattr(first_card, "subject", None)
            or getattr(first_card, "requirement", None)
            or getattr(first_card, "part", "")
            or section_name
        ).strip() or section_name,
        "section_summary": "",
        # 여기서 말하는 블록은 섹션 안에서 카드로 먼저 분리된 뒤,
        # 그 카드 내부에서 본문/표로 다시 나뉘어 생성되는 개별 조각을 뜻한다.
        "hierarchy_guidance": sequence_text or "섹션 블록(카드 내부 본문/표 조각) 단위로 계층을 추론",
        "body_hierarchy_profile": body_hierarchy_profile,
        "body_block_rulesets": [
            {
                # 블록 = 카드 내부의 본문/표 조각 단위
                "block_kind": "shared_parent" if body_hierarchy_profile.get("uses_shared_parent_root") else "hierarchical",
                "family_sequence": body_hierarchy_profile.get("family_sequence") or [],
                "root_family": body_hierarchy_profile.get("root_family") or "",
                "requirement_family": body_hierarchy_profile.get("requirement_family") or "",
                "detail_family": body_hierarchy_profile.get("detail_family") or "",
            }
        ],
    }


def _build_section_context_via_llm(client, section_name: str, cards: list[RfpCard], model_name: str) -> tuple[dict, dict, str]:
    payload = {
        "section_name": section_name,
        "cards": [
            {
                "card_no": getattr(card, "card_no", None) or card.card_id,
                "requirement": card.requirement,
                "subject": getattr(card, "subject", None),
                "sub_subject": getattr(card, "sub_subject", None),
                "has_table": "<table" in str(card.html_excerpt or "").lower(),
                "plain_text": _plain_text_from_html_excerpt(str(card.html_excerpt or ""))[:5000],
            }
            for card in cards
        ],
    }
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You normalize one RFP section only. "
                    "Return JSON object only with keys: section_title, id_prefix, default_item_name, section_summary, hierarchy_guidance. "
                    "Use the card plain text and source HTML structure to understand hierarchy, list nesting, heading emphasis, and table/list boundaries. "
                    "Ignore style/class/id and focus only on semantic structure and source text. "
                    "id_prefix must be a short Korean noun-like label derived from the section title or section theme. "
                    "hierarchy_guidance should briefly explain how item_name / requirement / detailed_requirement should align in this section."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        **_responses_create_kwargs(model_name),
    )
    raw_content = (getattr(response, "output_text", None) or "").strip()
    parsed = _extract_json_payload(raw_content)
    if not isinstance(parsed, dict):
        parsed = {}
    context = _section_context_fallback(section_name, cards)
    context.update(
        {
            "section_title": str(parsed.get("section_title") or context["section_title"]).strip() or context["section_title"],
            "id_prefix": str(parsed.get("id_prefix") or context["id_prefix"]).strip() or context["id_prefix"],
            "default_item_name": str(parsed.get("default_item_name") or context["default_item_name"]).strip() or context["default_item_name"],
            "section_summary": str(parsed.get("section_summary") or "").strip(),
            "hierarchy_guidance": str(parsed.get("hierarchy_guidance") or "").strip(),
        }
    )
    input_tokens, output_tokens = _extract_token_usage(response)
    usage = {
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, input_tokens, output_tokens),
    }
    return context, usage, raw_content


def _fallback_card_requirement_rows(card: RfpCard, section_context: dict) -> list[dict]:
    lines = [line.strip(" -\u2022\t") for line in _html_excerpt_lines(str(card.html_excerpt or "")) if line.strip()]
    title = str(getattr(card, "subject", None) or card.requirement or "").strip() or str(card.requirement)
    item_name = str(getattr(card, "sub_subject", None) or section_context.get("default_item_name") or section_context.get("section_title") or title).strip()
    content_lines = [line for line in lines if line != title]
    if not content_lines:
        content_lines = [title]
    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for line in content_lines[:20]:
        requirement = title
        detail_requirement = re.sub(r"\s+", " ", line).strip()
        for detail_unit in _split_atomic_detail_units(detail_requirement) or [detail_requirement]:
            key = (item_name, requirement, detail_unit)
            if not detail_unit or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": detail_unit,
                    "result_note": "",
                    "build_method": "룰 기반(fallback)",
                }
            )
    return rows or [{"item_name": item_name, "requirement": title, "detail_requirement": title, "result_note": "", "build_method": "룰 기반(fallback)"}]


def _fallback_table_card_rows(card: RfpCard, section_context: dict) -> list[dict]:
    # 표 파싱에 실패한 표 카드에 대해, LLM을 호출하지 않고 룰 기반으로 강제 분할 추출하는 안전망 함수
    html_excerpt = str(card.html_excerpt or "").strip()
    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    
    rows: list[dict] = []
    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    item_name = str(getattr(card, "sub_subject", None) or section_context.get("default_item_name") or section_context.get("section_title") or card_title).strip()
    
    seen: set[tuple[str, str, str]] = set()
    
    for table in tables:
        records = _table_row_records_linewise(table)
        if not records:
            continue
        first_tr = table.find("tr")
        has_header_row = bool(first_tr and first_tr.find("th"))
        header_row = [str(cell or "").strip() for cell in records[0][1]] if records else []

        data_rows = records[1:] if has_header_row and len(records) > 1 else records

        for _, row in data_rows:
            # 빈 셀 제외한 유효 텍스트들
            cells = [str(c).strip() for c in row if str(c).strip()]
            if not cells:
                continue
            
            # 셀 개수에 따른 휴리스틱 매핑
            if len(cells) == 1:
                requirement = card_title
                detail_requirement = cells[0]
            elif len(cells) == 2:
                requirement = cells[0]
                detail_requirement = cells[1]
            else:
                # 3개 이상인 경우: 마지막 셀을 detail_requirement로 하고 앞의 셀들은 requirement로 결합
                requirement = " > ".join(cells[:-1])
                detail_requirement = cells[-1]
                
            requirement = _normalize_requirement_text(requirement)
            detail_requirement = _normalize_requirement_text(detail_requirement)
            # fallback 경로는 HTML 줄 단위 결과를 더 쪼개지 않고 그대로 유지한다.
            # 여기서 추가 분해를 하면 <p>/<li> 기준으로 살아난 줄이 다시 합쳐지거나,
            # 긴 표 셀이 과도하게 여러 조각으로 갈라져 속도와 안정성이 떨어진다.
            fallback_detail_units = [
                _normalize_requirement_text(part)
                for part in detail_requirement.split("\n")
                if _normalize_requirement_text(part)
            ] or [detail_requirement]

            for detail_unit in fallback_detail_units:
                key = (item_name, requirement, detail_unit)
                if not detail_unit or key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "item_name": item_name,
                        "requirement": requirement,
                        "detail_requirement": detail_unit,
                        "result_note": "",
                        "build_method": "룰 기반(표 fallback)",
                    }
                )
                
    if not rows:
        return _fallback_card_requirement_rows(card, section_context)
    return rows


def _build_card_requirement_rows_via_llm(
    client,
    card: RfpCard,
    section_context: dict,
    model_name: str,
    *,
    use_llm: bool = True,
) -> tuple[list[dict], dict, str]:
    zero_usage = {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    section_context = dict(section_context or {})
    body_fragment_level = getattr(card, "body_fragment_level", None)
    if body_fragment_level is not None:
        section_context["body_fragment_level"] = body_fragment_level

    html_excerpt = str(card.html_excerpt or "")
    has_table_card = "<table" in html_excerpt.lower()
    has_nested_table_card = False
    if has_table_card:
        soup_for_nested = BeautifulSoup(html_excerpt, "html.parser")
        has_nested_table_card = any(
            _table_has_nested_table(table)
            for table in soup_for_nested.find_all("table")
            if table.find_parent("table") is None
        )
    force_llm_for_table_card = has_table_card and _should_route_table_card_to_llm(card)
    section_profile = (section_context or {}).get("body_hierarchy_profile") or {}

    rule_based_table_rows = [] if force_llm_for_table_card else _extract_rows_from_table_card(card, section_context=section_context)
    if rule_based_table_rows:
        return (
            rule_based_table_rows,
            zero_usage,
            "RULE_BASED_TABLE",
        )
    if has_table_card and not force_llm_for_table_card:
        fallback_table_rows = _fallback_table_card_rows(card, section_context)
        if fallback_table_rows:
            return (
                fallback_table_rows,
                zero_usage,
                "RULE_BASED_TABLE_FALLBACK",
            )

    html_lines = _html_excerpt_lines(html_excerpt, include_tables=has_table_card)
    body_text = "\n".join(html_lines)
    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    if not force_llm_for_table_card:
        atomic_rows = _extract_atomic_body_rows(
            html_lines,
            title=card_title,
            default_item_name=str(card_title or getattr(card, "sub_subject", None) or section_context.get("default_item_name") or section_context.get("section_title") or ""),
            build_method="룰 기반(단일 행 본문)",
        )
        if atomic_rows:
            return atomic_rows, zero_usage, "RULE_BASED_BODY"
    if not use_llm:
        fallback_rows = _fallback_table_card_rows(card, section_context) if has_table_card else _fallback_card_requirement_rows(card, section_context)
        return fallback_rows, zero_usage, "RULE_BASED_FALLBACK_NO_LLM"
    if not has_table_card:
        if _should_skip_llm_for_heading_only_body_card(body_text, card_title):
            return [], zero_usage, "SKIPPED_HEADING_ONLY_BODY"

    payload = {
        "section_context": section_context,
        "card": {
            "card_no": getattr(card, "card_no", None) or card.card_id,
            "requirement": card.requirement,
            "subject": getattr(card, "subject", None),
            "sub_subject": getattr(card, "sub_subject", None),
            "part": getattr(card, "part", None) or "",
            "section": card.section,
            "has_table": has_table_card,
            "has_nested_table": has_nested_table_card,
            "html_excerpt": html_excerpt[:16000],
            "html_lines": body_text[:12000],
        },
    }
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You convert one RFP card into atomic requirement table rows. "
                    "Return JSON array only. Each item must have keys: item_name, requirement, detail_requirement, result_note. "
                    "All output values must be plain text only, no HTML, no markdown. "
                    "Use the card HTML excerpt structure and extracted HTML lines as the primary source for hierarchy, nesting, list grouping, heading emphasis, and structural boundaries. "
                    "Treat each visible <p> or <li> line as an atomic source line unless the source explicitly keeps two lines in the same sentence. "
                    "Do not merge separate <p>/<li> lines back into one row just because they are adjacent in the same list item. "
                    "Ignore class/style/id and focus only on semantic structure and source text. "
                    "requirement must be a heading-like label or short requirement title, not a full explanatory sentence. "
                    "If explanatory prose is attached to a requirement heading, keep only the heading in requirement and move the prose into detail_requirement. "
                    "If a candidate requirement is a sentence, that means the layer is wrong; demote that sentence into detail_requirement and keep requirement as the nearest heading-like label instead. "
                    "detail_requirement must be atomic and specific, usually one sentence and at most two short sentences. "
                    "Preserve source order. Do not invent content. "
                    "Use the source text verbatim whenever possible; do not paraphrase, summarize, or rewrite. "
                    "Do not drop bullet markers, list markers, or line breaks that carry meaning in the source text. "
                    "If the source uses bullets such as -, *, •, ◦, □, keep those bullet markers in the output text. "
                    "When splitting one source block into multiple rows, each row must preserve the original bullet/list marker and wording of that unit. "
                    "If the source card contains a nested table, treat the nested table as a real lower-level structure and do not collapse it into the parent table row. "
                    "If one detailed line contains chained inline markers like '가. 나. 다. 라. ...', do not drop any of those markers; keep the full original marker chain in the resulting detail_requirement text. "
                    "For body content, first infer a clean hierarchy of 1-level, 2-level, 3-level, 4-level, or 5-level from the source structure. "
                    "Then map it strictly as follows: 1-level body => detail_requirement only; 2-level body => requirement + detail_requirement; 3-level body => item_name + requirement + detail_requirement; 4-level or 5-level body => item_name + requirement + merged detail_requirements. "
                    "When the body has only 1 visible level, use the card title or section context for the upper fields and place the actual body line in detail_requirement. "
                    "When the body has 2 visible levels, use the level-1 line as requirement and the level-2 line as detail_requirement. "
                    "When the body has 3 visible levels, use level-1 as item_name, level-2 as requirement, and level-3 as detail_requirement. "
                    "When the body has 4 or more visible levels (such as 5 levels), use level-1 as item_name, level-2 as requirement, and merge all subsequent levels (level-3, level-4, level-5, etc.) into detail_requirement (separated by newlines). "
                    "If a 3-level body detail_requirement contains more than two sentences, split it into multiple rows so each detail_requirement contains one sentence or at most two short sentences. "
                    "If the body has a shared parent heading such as '○ 공통' and child items labeled '가.', '나.', '다.', use the full shared parent heading line including its bullet marker as item_name, "
                    "use the full child heading line such as '가. ...', '나. ...', '다. ...' as requirement, and split each '- ' bullet under that child into separate detail_requirement rows. "
                    "If the card body starts with the card title and then has a child heading such as '가. ...' followed by bullet lines, use the card title as item_name, "
                    "use the full child heading line as requirement, and split each bullet line below it into separate detail_requirement rows. "
                    "For 3-level tables, map the hierarchy into item_name > requirement > detail_requirement. "
                    "For 2-level tables, use the section subheading or section context as the first level and merge the table rows under it. "
                    "For body requirements, infer a 3-level hierarchy from the text structure. "
                    "If one card contains multiple distinct detailed requirements, return multiple rows. "
                    "Never leave any of the three fields empty."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        **_responses_create_kwargs(model_name),
    )
    raw_content = (getattr(response, "output_text", None) or "").strip()
    parsed = _extract_json_payload(raw_content)
    rows: list[dict] = []
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            item_name = _normalize_requirement_text(str(item.get("item_name") or "").strip())
            requirement = _normalize_requirement_text(str(item.get("requirement") or "").strip())
            detail_requirement = _normalize_requirement_text(str(item.get("detail_requirement") or "").strip())
            if not detail_requirement:
                continue
            if not requirement:
                requirement = card_title or "요구사항"
            if not item_name:
                item_name = str(getattr(card, "sub_subject", None) or section_context.get("default_item_name") or section_context.get("section_title") or requirement).strip()
            rows.append(
                {
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": detail_requirement,
                    "result_note": _normalize_requirement_text(str(item.get("result_note") or "").strip()),
                    "id_title_hint": _normalize_requirement_text(str(item.get("id_title_hint") or "").strip()),
                    "build_method": "LLM",
                }
            )
    input_tokens, output_tokens = _extract_token_usage(response)
    usage = {
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, input_tokens, output_tokens),
    }
    return rows, usage, raw_content


def _build_table_followup_note_rows_via_llm(
    client,
    previous_table_card: RfpCard,
    note_card: RfpCard,
    section_context: dict,
    model_name: str,
) -> tuple[list[dict], dict, str]:
    zero_usage = {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    previous_html = str(getattr(previous_table_card, "html_excerpt", "") or "")
    note_html = str(getattr(note_card, "html_excerpt", "") or "")
    note_lines = _html_excerpt_lines(note_html, include_tables=False)
    note_text = "\n".join(note_lines)
    if not note_text.strip():
        return [], zero_usage, "FOLLOWUP_NOTE_EMPTY"

    previous_soup = BeautifulSoup(previous_html, "html.parser")
    previous_tables = previous_soup.find_all("table")
    target_table = previous_tables[-1] if previous_tables else None
    target_matrix = _table_visual_matrix(target_table) if target_table is not None else []
    card_title = str(getattr(previous_table_card, "subject", None) or previous_table_card.requirement or "").strip()
    leading_context = _leading_body_context_from_table_html(previous_html, card_title)
    top_table_context, _ = _top_table_context_from_matrix(target_matrix, card_title)

    fallback_heading = _normalize_requirement_text(
        str(top_table_context or leading_context or previous_table_card.requirement or note_card.requirement or "")
    )
    fallback_item_name = _normalize_requirement_text(
        str(section_context.get("default_item_name") or fallback_heading or note_card.requirement or "")
    )
    payload = {
        "section_context": section_context,
        "previous_table_card": {
            "card_no": getattr(previous_table_card, "card_no", None) or previous_table_card.card_id,
            "requirement": previous_table_card.requirement,
            "subject": getattr(previous_table_card, "subject", None),
            "sub_subject": getattr(previous_table_card, "sub_subject", None),
            "html_excerpt": previous_html[:16000],
            "html_lines": "\n".join(_html_excerpt_lines(previous_html, include_tables=False))[:12000],
        },
        "note_card": {
            "card_no": getattr(note_card, "card_no", None) or note_card.card_id,
            "requirement": note_card.requirement,
            "subject": getattr(note_card, "subject", None),
            "sub_subject": getattr(note_card, "sub_subject", None),
            "html_excerpt": note_html[:8000],
            "html_lines": note_text[:4000],
        },
        "fallback_heading": fallback_heading,
        "fallback_item_name": fallback_item_name,
    }
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You normalize one short note that follows a table in an RFP card. "
                    "This note starts with a '*' marker and applies to the whole preceding table, not to each row. "
                    "Return exactly one JSON object only with keys: item_name, requirement, detail_requirement, result_note, id_title_hint. "
                    "Use the previous table html excerpt and the following note together. "
                    "Infer the correct higher hierarchy from the table title, caption, leading context, and table headers. "
                    "Do not copy the explanatory note sentence into item_name or requirement. "
                    "Preserve the actual table requirement context in requirement; do not drop it. "
                    "If needed, use the table title or the table's third-column/header meaning as requirement so that the row still represents the table requirement itself. "
                    "detail_requirement must preserve the note text verbatim whenever possible. "
                    "item_name and requirement must be short heading-like labels, not long sentences. "
                    "id_title_hint must be a short noun-like label suitable for requirement ID generation. "
                    "Do not invent content. Plain text only, no markdown, no HTML."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        **_responses_create_kwargs(model_name),
    )
    raw_content = (getattr(response, "output_text", None) or "").strip()
    parsed = _extract_json_payload(raw_content)
    rows: list[dict] = []
    item = parsed if isinstance(parsed, dict) else None
    if item is not None:
        item_name = _normalize_requirement_text(str(item.get("item_name") or "").strip())
        requirement = _normalize_requirement_text(str(item.get("requirement") or "").strip())
        detail_requirement = _normalize_requirement_text(str(item.get("detail_requirement") or "").strip())
        if detail_requirement:
            if not item_name:
                item_name = fallback_item_name or fallback_heading or "공통"
            if not requirement:
                requirement = fallback_heading or item_name or "공통"
            rows.append(
                {
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": detail_requirement,
                    "result_note": _normalize_requirement_text(str(item.get("result_note") or "").strip()),
                    "id_title_hint": _normalize_requirement_text(
                        str(item.get("id_title_hint") or item_name or fallback_heading or "").strip()
                    ),
                    "build_method": "LLM(표후속공통주석)",
                }
            )
    input_tokens, output_tokens = _extract_token_usage(response)
    usage = {
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, input_tokens, output_tokens),
    }
    return rows, usage, raw_content


def _build_section_requirement_tables(
    cards: list[RfpCard],
    model_name: str,
    *,
    use_llm: bool = True,
    disable_dedup: bool = False,
) -> tuple[dict[str, list[dict]], list[dict], dict]:
    client = None
    if use_llm:
        api_key = _load_openai_api_key()
        if not api_key:
            raise RuntimeError("OPENAI API 키가 없습니다. 먼저 키를 저장해 주세요.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("`openai` 패키지가 필요합니다. requirements 설치를 다시 실행해주세요.") from exc

        client = OpenAI(api_key=api_key)
    expanded_cards: list[RfpCard] = []
    for card in cards:
        expanded_cards.extend(_partition_card_for_requirement_build(card))
    grouped_cards = _group_cards_by_section(expanded_cards)
    section_tables: dict[str, list[dict]] = {}
    debug_rows: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0

    progress = st.progress(0.0, text="섹션단위로 항목정리 준비 중")
    total_cards = sum(len(section_cards) for _, section_cards in grouped_cards) or 1
    processed_cards = 0
    two_level_table_methods = {
        "룰 기반(2단 표)",
        "룰 기반(3단 표->2단 표+추가정보)",
    }

    artifact_root: Path | None = None
    if st.session_state.get("file_name"):
        artifact_root = current_artifact_root()
        artifact_root.mkdir(parents=True, exist_ok=True)

    def _persist_debug_snapshot() -> None:
        if artifact_root is None:
            return
        usage_snapshot = {
            "model": model_name,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cost_usd": _estimate_llm_cost_usd(model_name, total_input_tokens, total_output_tokens),
        }
        try:
            (artifact_root / "section_requirement_debug_rows.json").write_text(
                json.dumps(debug_rows, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (artifact_root / "section_requirement_usage.json").write_text(
                json.dumps(usage_snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _merge_schedule_continuation_rows(rows: list[dict]) -> list[dict]:
        merged: list[dict] = []
        for row in rows:
            current_item = _normalize_requirement_text(str(row.get("item_name") or ""))
            current_req = _normalize_requirement_text(str(row.get("requirement") or ""))
            current_detail = _normalize_requirement_text(str(row.get("detail_requirement") or ""))
            current_note = _normalize_requirement_text(str(row.get("result_note") or ""))
            if merged:
                prev = merged[-1]
                prev_item = _normalize_requirement_text(str(prev.get("item_name") or ""))
                prev_req = _normalize_requirement_text(str(prev.get("requirement") or ""))
                prev_detail = _normalize_requirement_text(str(prev.get("detail_requirement") or ""))
                prev_note = _normalize_requirement_text(str(prev.get("result_note") or ""))
                if (
                    prev_item
                    and prev_item == current_item
                    and prev_req == current_req
                    and prev_note == current_note
                    and re.match(r"^[\-*·ㆍ−–—]+\s*.+$", prev_detail)
                    and re.match(r"^M\s*-\s*\d+(?:\s*(?:개월|주|일|개월차|주차|월|년|단계|시점|전|후))?.+$", current_detail, flags=re.IGNORECASE)
                ):
                    merged[-1] = {
                        **prev,
                        "detail_requirement": f"{prev_detail}\n{current_detail}".strip(),
                    }
                    continue
            merged.append(row)
        return merged

    for section_index, (section_name, section_cards) in enumerate(grouped_cards, start=1):
        progress.progress(processed_cards / total_cards, text=f"섹션 컨텍스트 생성 중: {section_name}")
        # 불필요한 LLM 호출을 건너뛰고 바로 룰 기반 섹션 컨텍스트를 사용하여 속도를 크게 향상시킵니다.
        section_context = _section_context_fallback(section_name, section_cards)
        section_usage = {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        section_raw = "RULE_BASED_CONTEXT"
        total_input_tokens += int(section_usage.get("input_tokens", 0) or 0)
        total_output_tokens += int(section_usage.get("output_tokens", 0) or 0)

        prefix = _section_requirement_prefix(str(section_context.get("id_prefix") or section_name))
        section_rows: list[dict] = []
        previous_section_card: RfpCard | None = None

        for card in section_cards:
            processed_cards += 1
            card_label = f"{section_name} / {getattr(card, 'card_no', card.card_id)}"
            st.session_state["step5_section_requirement_status"] = card_label
            progress.progress(min(processed_cards / total_cards, 1.0), text=f"카드 분석 중: {card_label}")
            import time
            t0 = time.time()
            stage_trace: list[dict] = []

            def _trace(stage_name: str, **payload) -> None:
                stage_trace.append(
                    {
                        "stage": stage_name,
                        "elapsed_seconds": round(time.time() - t0, 4),
                        **payload,
                    }
                )
                if debug_rows:
                    debug_rows[-1]["trace"] = list(stage_trace)
                    debug_rows[-1]["last_completed_stage"] = stage_trace[-1]["stage"] if stage_trace else ""
                    _persist_debug_snapshot()

            sub_subject = str(getattr(card, "sub_subject", "") or "").strip()
            build_source = "표" if sub_subject.startswith("표") or "<table" in str(card.html_excerpt or "").lower() else "본문"
            current_debug_row = {
                "section": section_name,
                "card_no": getattr(card, "card_no", None) or card.card_id,
                "subject": getattr(card, "subject", None) or card.requirement,
                "build_source": build_source,
                "body_fragment_level": getattr(card, "body_fragment_level", None),
                "raw_row_count": 0,
                "expanded_row_count": 0,
                "merged_row_count": 0,
                "normalized_row_count": 0,
                "saved_row_count": 0,
                "filtered_out_reason": "",
                "plain_text": "",
                "html_excerpt": "",
                "normalized_rows": [],
                "save_preview_rows": [],
                "context_raw": section_raw[:3000],
                "card_raw": "",
                "elapsed_seconds": 0.0,
                "llm_called": False,
                "llm_call_count": 0,
                "llm_stages": [],
                "trace": [],
                "last_completed_stage": "",
            }
            debug_rows.append(current_debug_row)
            _persist_debug_snapshot()
            _trace("card_start", build_source=build_source, sub_subject=sub_subject)
            is_table_followup_common_note = _is_table_followup_common_note_card(previous_section_card, card)
            inherit_prev_table_prefix = (
                _inherits_requirement_id_from_previous_table(previous_section_card, card)
                and not is_table_followup_common_note
            )
            debug_plain_text = _plain_text_from_html_excerpt(str(card.html_excerpt or ""))
            current_debug_row["plain_text"] = debug_plain_text[:6000]
            current_debug_row["html_excerpt"] = str(card.html_excerpt or "")[:12000]
            current_debug_row["body_rule_debug"] = _summarize_body_rule_debug(
                debug_plain_text,
                title=str(card.subject or card.requirement or ""),
                default_item_name=str(section_context.get("default_item_name") or ""),
                section_context=section_context,
            )
            current_debug_row["table_rule_debug"] = _summarize_table_card_rule_debug(card, section_context)
            _persist_debug_snapshot()
            skip_card, skip_reason = _should_skip_requirement_extraction(card)
            _trace("skip_check_done", skipped=skip_card, skip_reason=skip_reason)
            if skip_card:
                current_debug_row["card_raw"] = f"SKIPPED: {skip_reason}"
                current_debug_row["trace"] = list(stage_trace)
                current_debug_row["last_completed_stage"] = stage_trace[-1]["stage"] if stage_trace else ""
                _persist_debug_snapshot()
                previous_section_card = card
                continue
            debug_html_lines = _html_excerpt_lines(str(card.html_excerpt or ""), include_tables="<table" in str(card.html_excerpt or "").lower())
            current_debug_row["html_lines"] = "\n".join(debug_html_lines)[:12000]
            try:
                card_rows, card_usage, card_raw = _build_card_requirement_rows_via_llm(
                    client,
                    card,
                    section_context,
                    model_name,
                    use_llm=use_llm,
                )
            except Exception as exc:
                card_rows = []
                card_usage = {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
                card_raw = f"ERROR: {exc}"
            _trace(
                "build_rows_done",
                card_usage=card_usage,
                card_raw_prefix=str(card_raw)[:80],
                row_count=len(card_rows),
            )
            current_debug_row["raw_row_count"] = len(card_rows)
            t1 = time.time()
            total_input_tokens += int(card_usage.get("input_tokens", 0) or 0)
            total_output_tokens += int(card_usage.get("output_tokens", 0) or 0)
            card_rows = _expand_requirement_rows(card_rows)
            current_debug_row["expanded_row_count"] = len(card_rows)
            _trace("expand_rows_done", expanded_row_count=len(card_rows))

            item_name_usage = {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            item_name_llm_called = False
            if "<table" in str(card.html_excerpt or "").lower():
                eligible_table_rows = [
                    item
                    for item in card_rows
                    if isinstance(item, dict)
                    and _normalize_requirement_text(str(item.get("build_method") or "")) in two_level_table_methods
                ]
                if eligible_table_rows:
                    progress.progress(min(processed_cards / total_cards, 1.0), text=f"카드 분석 중(항목명 보정): {card_label}")
                    try:
                        inferred_item_name, item_name_usage = _infer_two_col_table_item_name_via_llm(
                            client,
                            card,
                            section_context,
                            model_name,
                        )
                        total_input_tokens += int(item_name_usage.get("input_tokens", 0) or 0)
                        total_output_tokens += int(item_name_usage.get("output_tokens", 0) or 0)
                        item_name_llm_called = bool(
                            int(item_name_usage.get("input_tokens", 0) or 0)
                            or int(item_name_usage.get("output_tokens", 0) or 0)
                        )
                        inferred_item_name = _normalize_requirement_text(inferred_item_name)
                        if inferred_item_name:
                            for item in eligible_table_rows:
                                item["item_name"] = inferred_item_name
                                build_method = _normalize_requirement_text(str(item.get("build_method") or ""))
                                if build_method and "항목명LLM" not in build_method:
                                    item["build_method"] = f"{build_method}+항목명LLM"
                    except Exception:
                        pass
                    _unify_similar_table_item_names(eligible_table_rows)
                _trace(
                    "table_item_name_inference_done",
                    eligible_table_rows=len(eligible_table_rows),
                    item_name_usage=item_name_usage,
                    item_name_llm_called=item_name_llm_called,
                )

            card_rows = _merge_schedule_continuation_rows(card_rows)
            current_debug_row["merged_row_count"] = len(card_rows)
            _trace("merge_schedule_done", merged_row_count=len(card_rows))

            normalized_row_preview = [
                {
                    "item_name": _normalize_requirement_text(str(item.get("item_name") or "")),
                    "requirement": _normalize_requirement_text(str(item.get("requirement") or "")),
                    "detail_requirement": _normalize_requirement_text(str(item.get("detail_requirement") or "")),
                    "result_note": _normalize_requirement_text(str(item.get("result_note") or "")),
                    "build_method": _normalize_requirement_text(str(item.get("build_method") or "")),
                    "table_rule_branch": _table_rule_branch_label(str(item.get("build_method") or "")) if build_source == "표" else "",
                }
                for item in card_rows
                if isinstance(item, dict)
            ]
            current_debug_row["normalized_row_count"] = len(normalized_row_preview)
            _trace("normalized_preview_done", normalized_row_count=len(normalized_row_preview))
            save_preview_rows: list[dict] = []

            progress.progress(min(processed_cards / total_cards, 1.0), text=f"결과 정리 중: {card_label}")
            current_debug_row["normalized_rows"] = normalized_row_preview
            current_debug_row["save_preview_rows"] = save_preview_rows
            current_debug_row["card_raw"] = card_raw[:6000]
            current_debug_row["elapsed_seconds"] = round(t1 - t0, 4)
            current_debug_row["llm_called"] = bool(
                int(card_usage.get("input_tokens", 0) or 0)
                or int(card_usage.get("output_tokens", 0) or 0)
                or item_name_llm_called
            )
            current_debug_row["llm_call_count"] = (
                int(
                    bool(
                        int(card_usage.get("input_tokens", 0) or 0)
                        or int(card_usage.get("output_tokens", 0) or 0)
                    )
                )
                + int(bool(item_name_llm_called))
            )
            current_debug_row["llm_stages"] = [
                stage_name
                for stage_name, called in (
                    ("build_card_requirement_rows", bool(
                        int(card_usage.get("input_tokens", 0) or 0)
                        or int(card_usage.get("output_tokens", 0) or 0)
                    )),
                    ("infer_two_col_table_item_name", bool(item_name_llm_called)),
                )
                if called
            ]
            current_debug_row["trace"] = list(stage_trace)
            current_debug_row["last_completed_stage"] = stage_trace[-1]["stage"] if stage_trace else ""
            _persist_debug_snapshot()

            for item in card_rows:
                block_name = _row_block_name(card, item, build_source)
                card_title_text = _normalize_requirement_text(
                    str(getattr(card, "subject", None) or card.requirement or "")
                )
                pre_save_item_name = _normalize_requirement_text(str(item.get("item_name") or ""))
                pre_save_requirement = _normalize_requirement_text(str(item.get("requirement") or ""))
                pre_save_detail_requirement = _normalize_requirement_text(str(item.get("detail_requirement") or ""))
                raw_item_name = _normalize_requirement_text(str(item.get("item_name") or ""))
                requirement = _strip_trailing_orphan_bullet(
                    pre_save_requirement,
                    source_tag=build_source,
                )
                detail_requirement = _strip_trailing_orphan_bullet(
                    pre_save_detail_requirement,
                    source_tag=build_source,
                )
                result_note = _strip_trailing_orphan_bullet(
                    _normalize_requirement_text(str(item.get("result_note") or "")),
                    source_tag=build_source,
                )
                special_rule_applied = bool(item.get("special_rule_applied"))
                build_method = _normalize_requirement_text(str(item.get("build_method") or "")) or (
                    "룰 기반"
                    if str(card_raw).startswith("RULE_BASED")
                    else "LLM"
                )
                build_method_before_save = build_method
                table_rule_branch = _table_rule_branch_label(build_method) if build_source == "표" else ""
                dash_mid_item_name = ""
                if special_rule_applied:
                    item_name = raw_item_name
                elif build_source == "본문":
                    # 본문은 atomic 평탄화만 허용하고, 저장 직전의 항목명/요구사항 승격은 하지 않는다.
                    item_name = raw_item_name
                else:
                    default_item_name_text = _normalize_requirement_text(str(section_context.get("default_item_name") or ""))
                    part_text = _normalize_requirement_text(str(getattr(card, "part", None) or ""))
                    if build_method.startswith("룰 기반(4단 표"):
                        item_name = raw_item_name
                    else:
                        item_name = _strip_trailing_orphan_bullet(
                            _normalize_item_name_for_row(
                                raw_item_name,
                                str(item.get("requirement") or ""),
                            )
                        )
                    card_plain_text = _plain_text_from_html_excerpt(str(card.html_excerpt or ""))
                    category_text = card_title_text
                    section_title_text = _normalize_requirement_text(
                        str(section_context.get("section_title") or section_name or getattr(card, "section", "") or "")
                    )
                    item_name = _fallback_item_name_for_marker_only_row(
                        item_name,
                        category_text,
                        section_title_text,
                        default_item_name_text,
                    )
                    item_name = _normalize_table_item_name_with_card_title(
                        item_name,
                        card_title=card_title_text,
                        default_item_name=default_item_name_text,
                        card_requirement=str(card.requirement or ""),
                        section_title=section_title_text,
                        part_text=part_text,
                    )
                    korean_heading_before_dash = _infer_korean_heading_before_dash_detail(card_plain_text or str(card.subject or card.requirement or ""))
                    if (
                        _should_override_requirement_with_korean_heading(build_source)
                        and korean_heading_before_dash
                        and item_name == requirement
                        and _is_title_like_requirement_text(item_name)
                        and re.match(r"^[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+\s*", detail_requirement)
                    ):
                        requirement = korean_heading_before_dash
                    detail_is_dash_like = bool(re.match(r"^[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+\s*", detail_requirement))
                    if (
                        item_name.startswith("-")
                        and detail_is_dash_like
                        and re.match(r"^ㄱ[\.\)]\s*", requirement) is None
                        and (category_text or section_title_text)
                    ):
                        fallback_title = category_text or default_item_name_text or section_title_text
                        item_name = fallback_title
                        requirement = fallback_title
                    has_followup_korean_jamo = bool(
                        re.search(r"^ㄱ[\.\)]\s*", card_plain_text, flags=re.MULTILINE)
                    )
                    if item_name.startswith("-") and not has_followup_korean_jamo:
                        context_item_name = category_text or default_item_name_text or section_title_text
                        if context_item_name and not context_item_name.startswith("-"):
                            item_name = context_item_name
                    dash_mid_item_name = _infer_dash_mid_item_name_from_text(card_plain_text or str(card.subject or card.requirement or ""))
                if build_source != "본문" and dash_mid_item_name and (
                        not item_name
                        or item_name == _normalize_requirement_text(str(card.subject or ""))
                        or item_name == default_item_name_text
                    ):
                        item_name = dash_mid_item_name
                if build_source != "본문":
                    item_name = _normalize_table_item_name_with_card_title(
                        item_name,
                        card_title=card_title_text,
                        default_item_name=default_item_name_text,
                        card_requirement=str(card.requirement or ""),
                        section_title=section_title_text,
                        part_text=part_text,
                    )
                requirement, detail_requirement = _flatten_body_requirement_for_save(
                    item_name,
                    requirement,
                    detail_requirement,
                    build_source=build_source,
                    special_rule_applied=special_rule_applied,
                )
                described_build_method, applied_rule = _describe_build_method(
                    build_method,
                    build_source,
                    item_name=item_name,
                    requirement=requirement,
                    detail_requirement=detail_requirement,
                )
                display_build_method = described_build_method
                if build_source == "표" and table_rule_branch:
                    display_build_method = f"{described_build_method} / 분기:{table_rule_branch}"
                source_detail_label = _build_source_detail_label(
                    build_source,
                    described_build_method,
                    item_name=item_name,
                    requirement=requirement,
                    detail_requirement=detail_requirement,
                    source_hint=str(item.get("build_source_detail") or ""),
                )
                is_box_style_three_col = described_build_method.startswith("룰 기반(3단 표-박스형 본문)")
                header_like_row = _is_header_like_requirement_row(item_name, requirement, detail_requirement, result_note)
                if is_box_style_three_col and detail_requirement and item_name and requirement and item_name == requirement:
                    header_like_row = False
                if (
                    not item_name
                    or not requirement
                    or header_like_row
                    or (not disable_dedup and _is_redundant_same_text_requirement_row(item_name, requirement, detail_requirement))
                ):
                    continue
                save_preview_rows.append(
                    {
                        "block_name": block_name,
                        "item_name_before_save": pre_save_item_name,
                        "requirement_before_save": pre_save_requirement,
                        "detail_requirement_before_save": pre_save_detail_requirement,
                        "build_method_before_save": build_method_before_save,
                        "table_rule_branch": table_rule_branch,
                        "item_name": item_name,
                        "requirement": requirement,
                        "detail_requirement": detail_requirement,
                        "result_note": result_note,
                        "build_method": display_build_method,
                        "build_source_detail": source_detail_label,
                        "applied_rule": applied_rule,
                    }
                )
                section_rows.append(
                    {
                        "블럭명": block_name,
                        "요구사항 ID": "",
                        "항목명": item_name,
                        "요구사항": requirement,
                        "상세요건": detail_requirement,
                        "추가정보": result_note,
                        "페이지": card.page_idx if isinstance(card.page_idx, int) else "",
                        "Part": str(getattr(card, "part", None) or "").strip(),
                        "Section": str(getattr(card, "section", None) or "").strip(),
                        "Category": str(getattr(card, "category", None) or "").strip(),
                        "생성 출처": source_detail_label,
                        "생성 방식": display_build_method,
                        "표 분기": table_rule_branch,
                        "_id_prefix_hint": _normalize_requirement_text(str(item.get("id_prefix_hint") or "")),
                        "_id_title_hint": _normalize_requirement_text(str(item.get("id_title_hint") or "")),
                        "_card_no": str(getattr(card, "card_no", None) or ""),
                        "_inherit_prev_table_prefix": inherit_prev_table_prefix,
                    }
                )
            current_debug_row["saved_row_count"] = len(save_preview_rows)
            if len(save_preview_rows) == 0:
                if len(card_rows) == 0:
                    current_debug_row["filtered_out_reason"] = "normalize/merge 이후 살아남은 행이 없음"
                else:
                    current_debug_row["filtered_out_reason"] = "헤더/중복/동일문구 필터에 의해 제거됨"
            _trace("save_rows_done", saved_row_count=len(save_preview_rows))
            current_debug_row["trace"] = list(stage_trace)
            current_debug_row["last_completed_stage"] = stage_trace[-1]["stage"] if stage_trace else ""
            _persist_debug_snapshot()
            previous_section_card = card

        progress.progress(min(processed_cards / total_cards, 1.0), text=f"요구사항 ID 생성 중: {section_name}")
        prefix_counters: dict[str, int] = {}
        last_row_prefix = ""
        for row in section_rows:
            inherit_prev_table_prefix = bool(row.get("_inherit_prev_table_prefix"))
            id_source = _select_requirement_id_source(
                str(row.get("항목명") or ""),
                str(row.get("요구사항") or ""),
                str(row.get("Category") or row.get("카테고리") or ""),
                section_name,
                str(row.get("_id_prefix_hint") or ""),
                str(row.get("_id_title_hint") or ""),
            )
            category_text = str(row.get("Category") or row.get("카테고리") or "")
            row_prefix = ""
            if inherit_prev_table_prefix and last_row_prefix:
                row_prefix = last_row_prefix
            else:
                row_prefix = _row_requirement_id_prefix(id_source, category_text, section_name, prefix)
            prefix_counters[row_prefix] = prefix_counters.get(row_prefix, 0) + 1
            row["요구사항 ID"] = f"{row_prefix}_{prefix_counters[row_prefix]:03d}"
            last_row_prefix = row_prefix
            row.pop("_id_prefix_hint", None)
            row.pop("_id_title_hint", None)
            row.pop("_card_no", None)
            row.pop("_inherit_prev_table_prefix", None)
        if section_rows:
            section_tables[section_name] = section_rows

    progress.progress(1.0, text="결과 렌더링 준비 중")
    st.session_state["step5_section_requirement_status"] = "결과 렌더링 준비 중"
    usage_info = {
        "model": model_name,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, total_input_tokens, total_output_tokens),
    }
    section_tables = _normalize_section_requirement_tables(section_tables)
    return section_tables, debug_rows, usage_info


def render_step2(
    keep_artifacts: bool,
    mark_running: Callable[[str, str], None],
    mark_done: Callable[[str, str], None],
    mark_error: Callable[[str, str], None],
    render_cards: Callable[[list], None],
    mode: str = "all",
) -> None:
    titles = {
        "all": ("HTML 문서 섹션 분리 카드 생성", "확정한 최종 목차를 기준으로 HTML 문서를 섹션 단위 카드로 생성합니다."),
        "cards": ("4. HTML 문서 섹션 분리 카드 생성", "확정한 최종 목차를 기준으로 HTML 문서를 섹션 단위 카드로 생성합니다."),
        "split": ("6. 부록 - 카드 분리", "생성된 카드를 표와 본문 또는 표 항목 기준으로 분리합니다."),
        "section_items": ("5. 카드 단위 조견표 생성", "생성된 카드를 기준으로 카드 단위 조견표를 생성합니다."),
    }
    title_text, body_text = titles.get(mode, titles["all"])
    st.subheader(title_text)
    st.write(body_text)
    if st.session_state.get("step2_html_source"):
        st.caption(f"카드 생성용 HTML 소스: `{st.session_state['step2_html_source']}`")

    if st.session_state["saved_toc_items"] is None:
        st.info("이 단계는 2단계에서 `최종 목차 저장` 후 사용할 수 있습니다.")
        return

    def _render_cards_section() -> None:
        if st.button("카드 생성", type="primary", use_container_width=True, key="run_step3_by_toc"):
            try:
                _run_step3(
                    keep_artifacts,
                    mark_running=mark_running,
                    mark_done=mark_done,
                    step_key="step4",
                )
                st.session_state["cards_step2_split"] = None
            except Exception as exc:  # noqa: BLE001
                mark_error("step4", str(exc))
                st.exception(exc)
        cards = st.session_state["cards_step2"] or []
        hide_generated_cards = st.checkbox(
            "생성된 카드 숨기기",
            value=bool(st.session_state.get("step4_hide_generated_cards", False)),
            key="step4_hide_generated_cards",
            help="체크하면 4단계에서 생성된 카드 본문과 카드 뷰를 숨기고, 다운로드와 디버그만 먼저 볼 수 있습니다.",
        )
        if cards:
            try:
                xlsx_bytes = _cards_to_workbook_bytes(cards)
                st.download_button(
                    "엑셀로 조견표 내보내기",
                    data=xlsx_bytes,
                    file_name=f"{Path(st.session_state.get('file_name') or 'document').stem}.cards.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            except Exception as exc:  # noqa: BLE001
                st.warning(f"엑셀 내보내기를 사용할 수 없습니다: {exc}")
        if hide_generated_cards:
            st.caption(f"생성된 카드 {len(cards)}건을 숨겼습니다.")
        else:
            render_cards(cards)

        match_debug = st.session_state.get("step3_toc_match_debug") or []
        if match_debug:
            matched_count = sum(1 for row in match_debug if row.get("matched"))
            unmatched_rows = [row for row in match_debug if not row.get("matched")]
            mismatched_rows = [
                row
                for row in match_debug
                if row.get("matched_index") is not None
                and row.get("resolved_index") is not None
                and row.get("matched_index") != row.get("resolved_index")
            ]
            st.markdown("**목차-본문 매칭 디버그**")
            st.caption(
                f"전체 {len(match_debug)}건 | 매칭 {matched_count}건 | 실패 {len(unmatched_rows)}건 | "
                f"예비/실제 인덱스 불일치 {len(mismatched_rows)}건"
            )
            render_safe_table(
                [
                    {
                        "title": row.get("title"),
                        "match_anchor": row.get("match_anchor"),
                        "matched_by": row.get("matched_by"),
                        "match_query": row.get("match_key"),
                        "toc_index": row.get("toc_index"),
                        "level": row.get("level"),
                        "matched": row.get("matched"),
                        "matched_index": row.get("matched_index"),
                        "resolved_index": row.get("resolved_index"),
                        "reason": row.get("reason"),
                        "matched_candidate": row.get("matched_candidate"),
                        "matched_block_text": row.get("matched_block_text") or row.get("matched_text"),
                    }
                    for row in match_debug
                ]
            )
            if mismatched_rows:
                with st.expander("예비 탐색과 실제 분리 인덱스가 다른 항목", expanded=False):
                    render_safe_table(
                        [
                            {
                                "title": row.get("title"),
                                "match_anchor": row.get("match_anchor"),
                                "matched_by": row.get("matched_by"),
                                "toc_index": row.get("toc_index"),
                                "level": row.get("level"),
                                "matched_index": row.get("matched_index"),
                                "resolved_index": row.get("resolved_index"),
                                "reason": row.get("reason"),
                                "matched_candidate": row.get("matched_candidate"),
                                "matched_block_text": row.get("matched_block_text") or row.get("matched_text"),
                            }
                            for row in mismatched_rows
                        ]
                    )
            if unmatched_rows:
                with st.expander("매칭 실패 항목 상세", expanded=False):
                    for row in unmatched_rows:
                        st.markdown(f"**{row.get('title', '')}**")
                        st.caption(
                            f"title={row.get('title')} | match_anchor={row.get('match_anchor')} | "
                            f"matched_by={row.get('matched_by')} | toc_index={row.get('toc_index')} | "
                            f"level={row.get('level')} | start_from={row.get('start_from')} | reason={row.get('reason')}"
                        )
                        if row.get("all_candidate_hits"):
                            st.write("후보 히트")
                            st.json(row.get("all_candidate_hits"))
                        nearby = row.get("nearby_blocks") or []
                        if nearby:
                            st.write("주변 본문 블록")
                            st.json(nearby)
                        st.divider()

    def _render_split_section() -> None:
        split_source_cards = st.session_state.get("cards_step2") or []
        if not split_source_cards:
            st.info("먼저 카드 생성 결과를 만들어 주세요.")
            return

        tab_split_stage1, tab_split_table = st.tabs(["표와 본문 나누기", "표 항목으로 나누기"])

        with tab_split_stage1:
            st.write("선택한 카드를 먼저 표와 본문으로 나눕니다.")
            split_options = {
                f"{getattr(card, 'card_no', None) or card.card_id} | {card.requirement}": card
                for card in split_source_cards
            }
            option_labels = list(split_options.keys())
            default_selected = st.session_state.get("cards_step2_split_selected_ids") or []
            preselected = [label for label in option_labels if label in default_selected]
            selected_labels = st.multiselect(
                "분리할 카드 선택",
                options=option_labels,
                default=preselected or option_labels,
                key="step2_split_selected_labels",
                help="체크한 카드만 표와 본문 나누기 대상으로 보냅니다.",
            )
            selected_cards = [split_options[label] for label in selected_labels]
            st.session_state["cards_step2_split_selected_ids"] = selected_labels
            st.caption(f"선택된 카드: {len(selected_cards)} / {len(split_source_cards)}")

            if st.button("표와 본문 나누기 실행", type="primary", use_container_width=True, key="run_step2_split_stage1"):
                try:
                    if not selected_cards:
                        st.warning("분리할 카드를 하나 이상 선택해 주세요.")
                        st.session_state["cards_step2_split_stage1"] = []
                        return
                    stage1_cards: list[RfpCard] = []
                    for card in selected_cards:
                        stage1_cards.extend(_partition_card_into_table_body_segments(card))
                    st.session_state["cards_step2_split_stage1"] = stage1_cards
                    st.session_state["cards_step2_split"] = None
                    st.success(f"표와 본문 나누기 {len(stage1_cards)}건을 생성했습니다.")
                except Exception as exc:  # noqa: BLE001
                    mark_error("step6", str(exc))
                    st.exception(exc)

            stage1_cards = st.session_state.get("cards_step2_split_stage1") or []
            if stage1_cards:
                st.caption(f"분리 결과: {len(stage1_cards)}건")
                render_cards(stage1_cards)

        with tab_split_table:
            stage1_cards = st.session_state.get("cards_step2_split_stage1") or []
            if not stage1_cards:
                st.info("먼저 `표와 본문 나누기` 탭에서 1단계 분리를 실행해 주세요.")
            else:
                table_cards_source = [
                    card
                    for card in stage1_cards
                    if "<table" in str(card.html_excerpt or "").lower()
                ]
                st.caption(f"표와 본문 나누기 결과 전체 카드: {len(stage1_cards)}건, 표 카드: {len(table_cards_source)}건")
                if st.button("표 항목으로 나누기 실행", type="primary", use_container_width=True, key="run_step2_split_table"):
                    try:
                        table_cards: list[RfpCard] = []
                        for card in stage1_cards:
                            table_cards.extend(_partition_table_cards_by_columns(card))
                        st.session_state["cards_step2_split_table"] = table_cards
                        st.session_state["cards_step2_split"] = None
                        st.success(f"표 항목으로 나누기 {len(table_cards)}건을 생성했습니다.")
                    except Exception as exc:  # noqa: BLE001
                        mark_error("step6", str(exc))
                        st.exception(exc)

                table_cards = st.session_state.get("cards_step2_split_table") or []
                if table_cards:
                    st.caption(f"분리 결과: {len(table_cards)}건")
                    span_count = sum(1 for card in table_cards if "병합셀" in str(card.sub_subject or ""))
                    if span_count:
                        st.caption(f"rowspan/colspan 포함 표: {span_count}건")
                    render_cards(table_cards)

    def _render_section_items_section() -> None:
        source_cards = st.session_state.get("cards_step2") or []
        if not source_cards:
            st.info("먼저 카드 생성 결과를 만들어 주세요.")
            return

        section_groups = _group_cards_by_section(source_cards)
        section_name_to_cards = {section_name: cards for section_name, cards in section_groups}
        section_names = list(section_name_to_cards.keys())
        section_option_map = {
            f"{section_name} ({len(cards)}건)": section_name
            for section_name, cards in section_groups
        }
        section_option_labels = list(section_option_map.keys())
        default_selected_sections = st.session_state.get("cards_step2_section_selected_names") or section_names
        default_selected_labels = [
            option_label
            for option_label, section_name in section_option_map.items()
            if section_name in default_selected_sections
        ]
        selected_section_names = st.multiselect(
            "정리할 섹션 선택",
            options=section_option_labels,
            default=default_selected_labels or section_option_labels,
            key="step2_section_requirement_selected_names",
            help="선택한 섹션만 섹션단위로 항목정리를 실행합니다.",
        )
        resolved_selected_section_names = [
            section_option_map[label]
            for label in selected_section_names
            if label in section_option_map
        ]
        st.session_state["cards_step2_section_selected_names"] = resolved_selected_section_names
        st.checkbox(
            "조견표 항목 완전 중복 제거 하지 않기",
            value=bool(st.session_state.get("step5_section_requirement_disable_dedup", False)),
            key="step5_section_requirement_disable_dedup",
            help="체크하면 같은 행이 카드 간/카드 내부에서 반복되어도 그대로 남깁니다.",
        )
        selected_cards: list[RfpCard] = []
        for section_name in resolved_selected_section_names:
            selected_cards.extend(section_name_to_cards.get(section_name, []))
        current_signature = _section_requirement_cards_signature(selected_cards)
        cached_signature = st.session_state.get("cards_step2_section_requirement_signature")
        if cached_signature != current_signature:
            _clear_section_requirement_cache()
        st.caption(
            f"전체 섹션 수: {len(section_groups)}개 | 선택 섹션 수: {len(resolved_selected_section_names)}개 | "
            f"선택 카드 수: {len(selected_cards)}개"
        )
        if st.button("섹션단위로 항목정리 실행", type="primary", use_container_width=True, key="run_step2_section_items"):
            try:
                if not selected_cards:
                    st.warning("정리할 섹션을 하나 이상 선택해 주세요.")
                    return
                model_name = st.session_state.get("llm_model", "gpt-4o")
                section_tables, debug_rows, usage_info = _build_section_requirement_tables(
                    selected_cards,
                    model_name,
                    use_llm=bool(st.session_state.get("step5_use_llm", False)),
                    disable_dedup=bool(st.session_state.get("step5_section_requirement_disable_dedup", False)),
                )
                st.session_state["cards_step2_section_requirement_tables"] = section_tables
                st.session_state["cards_step2_section_requirement_debug"] = debug_rows
                st.session_state["cards_step2_section_requirement_usage"] = usage_info
                st.session_state["cards_step2_section_requirement_signature"] = current_signature
                st.session_state["llm_cost_total_usd"] = float(st.session_state.get("llm_cost_total_usd", 0.0) or 0.0) + float(
                    usage_info.get("cost_usd", 0.0) or 0.0
                )
                st.session_state.setdefault("llm_cost_logs", []).append(usage_info)
                if keep_artifacts and st.session_state.get("file_name"):
                    artifact_root = current_artifact_root()
                    artifact_root.mkdir(parents=True, exist_ok=True)
                    (artifact_root / "section_requirement_tables.json").write_text(
                        json.dumps(section_tables, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    (artifact_root / "section_requirement_tables.xlsx").write_bytes(_section_rows_to_xlsx_bytes(section_tables))
                mark_done("step5", f"선택 섹션 {len(section_tables)}개 / 총 {sum(len(rows) for rows in section_tables.values())}건")
                st.success(
                    f"섹션단위로 항목정리 완료: 선택 섹션 {len(section_tables)}개 / 총 {sum(len(rows) for rows in section_tables.values())}건"
                )
            except Exception as exc:  # noqa: BLE001
                mark_error("step5", str(exc))
                st.exception(exc)

        section_tables = st.session_state.get("cards_step2_section_requirement_tables") or {}
        if section_tables:
            section_tables = _normalize_section_requirement_tables(section_tables)
            st.session_state["cards_step2_section_requirement_tables"] = section_tables
        usage_info = st.session_state.get("cards_step2_section_requirement_usage") or {}
        if section_tables:
            hide_generated_results = st.checkbox(
                "생성된 조견표 숨기기",
                value=bool(st.session_state.get("step5_section_requirement_hide_generated_results", False)),
                key="step5_section_requirement_hide_generated_results",
                help="체크하면 조견표 본문과 다운로드를 숨기고 디버그 로그를 먼저 볼 수 있습니다.",
            )
            if not hide_generated_results:
                st.caption(
                    f"생성 섹션: {len(section_tables)}개 | 총 행 수: {sum(len(rows) for rows in section_tables.values())}개"
                )
                if usage_info:
                    st.caption(
                        f"LLM 사용량: model={usage_info.get('model')} | in={usage_info.get('input_tokens', 0)} | "
                        f"out={usage_info.get('output_tokens', 0)} | cost=${usage_info.get('cost_usd', 0.0):.6f}"
                    )
                xlsx_bytes = _section_rows_to_xlsx_bytes(section_tables)
                st.download_button(
                    "섹션별 항목정리 엑셀 다운로드",
                    data=xlsx_bytes,
                    file_name=f"{Path(st.session_state.get('file_name') or 'document').stem}.section-requirements.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="download_step2_section_requirements_xlsx",
                )
                visible_section_items = [
                    (section_name, rows)
                    for section_name, rows in section_tables.items()
                    if not _is_trivial_single_requirement_section(rows)
                ]
                hidden_section_count = len(section_tables) - len(visible_section_items)
                if hidden_section_count:
                    st.caption(f"항목명=요구사항=상세요건 1행 섹션 {hidden_section_count}개는 탭에서 숨겼습니다.")
                section_tabs = st.tabs([_safe_sheet_name(name, f"Section{idx}") for idx, (name, _) in enumerate(visible_section_items, start=1)])
                for tab, (section_name, rows) in zip(section_tabs, visible_section_items, strict=False):
                    with tab:
                        st.caption(f"{section_name} | {len(rows)}건")
                        render_safe_table(
                            [
                                {
                                    **row,
                                    "Part": row.get("Part") or "-",
                                    "Section": row.get("Section") or "-",
                                    "Category": row.get("Category") or row.get("카테고리") or "-",
                                }
                                for row in rows
                            ]
                        )
            debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
            if debug_rows:
                with st.expander("섹션단위로 항목정리 디버그", expanded=False):
                    render_safe_table(
                        [
                            {
                                "section": row.get("section"),
                                "card_no": row.get("card_no"),
                                "subject": row.get("subject"),
                                "build_source": row.get("build_source"),
                                "table_rule_debug": " | ".join(
                                    f"{item.get('table_index')}:{item.get('branch')}"
                                    for item in (row.get("table_rule_debug") or [])
                                ),
                                "normalized_row_count": len(row.get("normalized_rows") or []),
                                "html_lines_preview": str(row.get("html_lines") or "")[:200],
                                "plain_text_preview": str(row.get("plain_text") or "")[:200],
                                "html_excerpt_preview": str(row.get("html_excerpt") or "")[:200],
                            }
                            for row in debug_rows
                        ]
                    )
                    for row in debug_rows:
                        label = f"{row.get('card_no')} | {row.get('subject')} | {row.get('build_source')}"
                        with st.expander(label, expanded=False):
                            left, right = st.columns(2)
                            with left:
                                st.markdown("**정규화된 행 결과**")
                                st.json(row.get("normalized_rows") or [])
                            with right:
                                st.markdown("**저장 직전 행 결과**")
                                st.json(row.get("save_preview_rows") or [])
                            if row.get("table_rule_debug"):
                                with st.expander("표 분기 디버그", expanded=False):
                                    st.json(row.get("table_rule_debug") or [])
                            with st.expander("원문/중간 데이터", expanded=False):
                                st.markdown("**HTML Lines**")
                                st.code(str(row.get("html_lines") or ""), language="text")
                                st.markdown("**Plain Text**")
                                st.code(str(row.get("plain_text") or ""), language="text")
                                st.markdown("**HTML Excerpt**")
                                st.code(str(row.get("html_excerpt") or ""), language="html")
                                st.markdown("**Section Context Raw**")
                                st.code(str(row.get("context_raw") or ""), language="text")
                                st.markdown("**Card Raw**")
                                st.code(str(row.get("card_raw") or ""), language="text")

    if mode == "all":
        tab_cards, tab_split, tab_section_items = st.tabs(["카드 생성", "카드 분리", "섹션단위로 항목정리"])
        with tab_cards:
            _render_cards_section()
        with tab_split:
            _render_split_section()
        with tab_section_items:
            _render_section_items_section()
        return

    if mode == "cards":
        _render_cards_section()
        return
    if mode == "split":
        _render_split_section()
        return
    if mode == "section_items":
        _render_section_items_section()
