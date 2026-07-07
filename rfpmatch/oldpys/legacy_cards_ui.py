from __future__ import annotations

from contextlib import nullcontext
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
from rfpmatch.models import RfpCard, Section, TocItem
from rfpmatch.toc_parser import _extract_lines_from_tag, _split_document_regions


_SECTION_REQUIREMENT_CACHE_VERSION = "2026-06-17-8"


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


def _is_trivial_single_requirement_section(rows: list[dict]) -> bool:
    if len(rows) != 1:
        return False
    row = rows[0] or {}

    def _norm(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    item_name = _norm(row.get("항목명") or row.get("item_name"))
    requirement = _norm(row.get("요구사항") or row.get("requirement"))
    detail_requirement = _norm(row.get("상세요건") or row.get("detail_requirement"))
    if not item_name or not requirement or not detail_requirement:
        return False
    if item_name == requirement == detail_requirement:
        return True
    part_value = _norm(row.get("Part") or row.get("part") or row.get("group"))
    section_value = _norm(row.get("Section") or row.get("section"))
    category_value = _norm(row.get("Category") or row.get("카테고리") or row.get("category") or row.get("requirement"))
    meta_label_hits = sum(1 for token in ("Part", "Section", "Category", "카테고리") if token.lower() in detail_requirement.lower())
    if meta_label_hits >= 2:
        return True
    if part_value and section_value and category_value:
        if part_value in detail_requirement and section_value in detail_requirement and category_value in detail_requirement:
            return True
    return False


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
            card.group,
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
                "group": getattr(card, "group", None),
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
                    "group": card.group,
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
                    "group": card.group,
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
                    "group": card.group,
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
        return actual_rest_key == expected_rest_key or actual_rest_key.startswith(expected_rest_key)

    if expected_prefix_key:
        return actual_prefix_key == expected_prefix_key

    expected_key = _title_key(expected_compact)
    actual_key = _title_key(actual_compact)
    if not expected_key or not actual_key:
        return False
    if expected_key == actual_key:
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
    if re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+)\s+", compact, flags=re.IGNORECASE):
        return True
    if re.match(r"^[가나다라마바사아자차카타파하]\.\s+", compact):
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


def _body_text_blocks(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    blocks: list[dict] = []
    candidates = body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "div", "section", "article", "figcaption"], recursive=True)
    seen_html: set[str] = set()
    for tag in candidates:
        if tag.name == "table":
            if tag.find_parent("table") is not None:
                continue
        else:
            if tag.find_parent(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "div", "section", "article", "figcaption"]) is not None:
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
        if html_chunk in seen_html:
            continue
        if tag.name == "table":
            seen_html.add(html_chunk)
            blocks.append({"html": html_chunk, "text": text, "tag": tag.name})
            continue

        if tag.name in {"h1", "h2", "h3", "h4", "h5", "h6", "figcaption"}:
            seen_html.add(html_chunk)
            blocks.append({"html": html_chunk, "text": text, "tag": tag.name})
            continue

        line_source = _tag_without_nested_tables(tag).find(tag.name) or tag
        lines = [line for line in _extract_lines_from_tag(line_source) if line.strip()]
        if len(lines) > 1:
            for line_idx, line in enumerate(lines, start=1):
                for part in _split_embedded_heading_suffixes(line):
                    line_html = f"<{tag.name}>{escape(part)}</{tag.name}>"
                    if line_html in seen_html:
                        continue
                    seen_html.add(line_html)
                    blocks.append({"html": line_html, "text": part, "tag": tag.name})
            continue

        split_parts = _split_embedded_heading_suffixes(text)
        if len(split_parts) > 1:
            for part in split_parts:
                part_html = f"<{tag.name}>{escape(part)}</{tag.name}>"
                if part_html in seen_html:
                    continue
                seen_html.add(part_html)
                blocks.append({"html": part_html, "text": part, "tag": tag.name})
            continue

        seen_html.add(html_chunk)
        blocks.append({"html": html_chunk, "text": text, "tag": tag.name})
    return blocks


def _block_title_candidates(block: dict) -> list[str]:
    candidates: list[str] = []
    text = str(block.get("text") or "").strip()
    if text:
        candidates.append(text)

    html = str(block.get("html") or "").strip()
    if html:
        soup = BeautifulSoup(html, "html.parser")
        raw_lines = soup.get_text("\n", strip=False).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for line in raw_lines:
            normalized = re.sub(r"\s+", " ", line).strip()
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        for cell in soup.find_all(["th", "td", "p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
            normalized = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    return candidates


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
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        for candidate in _block_title_candidates(block):
            if _title_match(title, candidate):
                return idx
    return None


def _debug_title_match_candidates(body_blocks: list[dict], title: str, start_from: int = 0, context_size: int = 2) -> dict:
    expected = _normalize_title_for_match(title)
    expected_key = _title_key(title)
    expected_prefix, expected_rest = _split_heading_prefix(title)
    debug: dict = {
        "title": title,
        "start_from": start_from,
        "expected": expected,
        "expected_key": expected_key,
        "expected_prefix": expected_prefix,
        "expected_rest": expected_rest,
        "matched": False,
        "matched_index": None,
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
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        for candidate in _block_title_candidates(block):
            if _title_match(title, candidate):
                best_match_index = idx
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
        debug["matched_text"] = block.get("text", "")
        debug["matched_tag"] = block.get("tag", "")
        debug["reason"] = best_match_reason
    else:
        debug["reason"] = "not_found_after_cursor"

    for idx, block in enumerate(body_blocks):
        for candidate in _block_title_candidates(block):
            if not _title_match(title, candidate):
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
    body_html = (st.session_state.get("step2_body_html") or "").strip()
    if body_html:
        body_blocks = _body_text_blocks(body_html)
    else:
        _, body_region = _split_document_regions(soup)
        body_blocks = _body_text_blocks(str(body_region))
    body_start_idx = _detect_body_start_index(body_blocks, toc_items)
    if body_start_idx > 0:
        body_blocks = body_blocks[body_start_idx:]
    matched_positions: list[tuple[int, int, TocItem]] = []
    match_debug: list[dict] = []
    candidate_cursor = 0
    for toc_idx, item in enumerate(toc_items):
        debug_entry = _debug_title_match_candidates(body_blocks, item.title, candidate_cursor)
        debug_entry["toc_index"] = toc_idx
        debug_entry["level"] = item.level
        debug_entry["page_idx"] = item.page_idx
        start_idx = _find_title_start_index(body_blocks, item.title, candidate_cursor)
        debug_entry["resolved_index"] = start_idx
        if start_idx is None:
            debug_entry["reason"] = debug_entry["reason"] or "not_found"
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
            if section_blocks:
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
    cards: list[RfpCard] = []
    stack: list[Section] = []
    for section in sections:
        while stack and stack[-1].level >= section.level:
            stack.pop()
        hierarchy = [*stack, section]
        level1_node = next((node for node in hierarchy if int(getattr(node, "level", 0) or 0) == 1), section)
        level2_node = next((node for node in hierarchy if int(getattr(node, "level", 0) or 0) == 2), None)
        level3_node = next((node for node in hierarchy if int(getattr(node, "level", 0) or 0) == 3), None)
        html_source = section.html if section.html.strip() else section.text
        cards.append(
                RfpCard(
                    card_id=len(cards) + 1,
                    card_no=str(len(cards) + 1),
                    requirement=section.title,
                    subject=section.title,
                    group=level1_node.title,
                    part=level1_node.title if level1_node else "",
                    section=level2_node.title if level2_node else "",
                    category=level3_node.title if level3_node else "",
                    html_excerpt=html_source.strip(),
                    page_idx=section.page_idx,
                anchor=section.anchor,
            )
        )
        stack.append(section)
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


def _clone_card(card: RfpCard, *, subject: str | None = None, card_no: str | None = None, card_id: int | None = None) -> RfpCard:
    return RfpCard(
        card_id=card_id if card_id is not None else card.card_id,
        card_no=card_no if card_no is not None else getattr(card, "card_no", None),
        requirement=card.requirement,
        subject=subject if subject is not None else (getattr(card, "subject", None) or card.requirement),
        sub_subject=getattr(card, "sub_subject", None),
        category=getattr(card, "category", None),
        part=getattr(card, "part", None) or card.group,
        group=card.group,
        section=card.section,
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
        "part": getattr(card, "part", None) or card.group,
        "group": card.group,
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
    }


def _partition_card_into_table_body_segments(card: RfpCard) -> list[RfpCard]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if not html_excerpt:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    blocks = _body_text_blocks(html_excerpt)
    if not blocks:
        return [_clone_card(card, subject=getattr(card, "subject", None) or card.requirement, card_no=getattr(card, "card_no", None) or str(card.card_id))]

    segments: list[tuple[str, list[dict]]] = []

    for block in blocks:
        kind = "table" if block["tag"] == "table" else "body"
        if kind == "table" and segments and segments[-1][0] == "body" and segments[-1][1]:
            prev_kind, prev_blocks = segments[-1]
            carried_block = prev_blocks.pop()
            if not prev_blocks:
                segments.pop()
            segments.append(("table", [carried_block, block]))
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
        stage1_cards.append(
            RfpCard(
                card_id=len(stage1_cards) + 1,
                card_no=f"{parent_no}-{idx}",
                requirement=card.requirement,
                subject=getattr(card, "subject", None) or card.requirement,
                sub_subject="표" if kind == "table" else "본문",
                group=card.group,
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
    for block in blocks:
        kind = "table" if block["tag"] == "table" else "body"
        if kind == "table" and segments and segments[-1][0] == "body" and segments[-1][1]:
            carried_block = segments[-1][1].pop()
            if not segments[-1][1]:
                segments.pop()
            segments.append(("table", [carried_block, block]))
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
        split_cards.append(
            RfpCard(
                card_id=len(split_cards) + 1,
                card_no=f"{parent_no}-rb{idx}",
                requirement=card.requirement,
                subject=getattr(card, "subject", None) or card.requirement,
                sub_subject="표" if kind == "table" else "본문",
                group=card.group,
                section=card.section,
                html_excerpt=segment_html,
                page_idx=card.page_idx,
                anchor=card.anchor,
            )
        )
    return split_cards or [card]


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
                group=card.group,
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
    for table in tables:
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
                    group=card.group,
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
                    group=card.group,
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
                    group=card.group,
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
                        group=str(item.get("group") or card.group).strip() or card.group,
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
    html = (
        st.session_state.get("html_raw_merged_empty_up_postprocessed")
        or st.session_state.get("html_merged_from_raw_postprocessed")
        or st.session_state.get("html_merged_from_postprocessed")
        or st.session_state.get("step2_merged_html")
        or st.session_state.get("html_merged_from_raw")
        or st.session_state.get("html_raw")
        or st.session_state["html"]
    )
    if st.session_state.get("html_raw_merged_empty_up_postprocessed"):
        st.session_state["step3_html_source"] = "html_raw_merged_empty_up_postprocessed"
    elif st.session_state.get("html_merged_from_raw_postprocessed"):
        st.session_state["step3_html_source"] = "html_merged_from_raw_postprocessed"
    elif st.session_state.get("html_merged_from_postprocessed"):
        st.session_state["step3_html_source"] = "html_merged_from_postprocessed"
    elif st.session_state.get("step2_merged_html"):
        st.session_state["step3_html_source"] = "step2_merged_html"
    elif st.session_state.get("html_merged_from_raw"):
        st.session_state["step3_html_source"] = "html_merged_from_raw"
    elif st.session_state.get("html_raw"):
        st.session_state["step3_html_source"] = "html_raw"
    else:
        st.session_state["step3_html_source"] = "html"
    toc_items = st.session_state["saved_toc_items"]
    if not html:
        st.warning("먼저 1단계를 완료해주세요.")
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
    _clear_section_requirement_cache()
    mark_done(step_key, f"조견표 카드 {len(cards)}건 생성 완료")

    if keep_artifacts and st.session_state["file_name"]:
        artifact_root = Path("artifacts") / Path(st.session_state["file_name"]).stem
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
            if re.match(r"^s\s+(?=[가-힣0-9(□※•▪■◆▶◦○◇·ㆍ\-–—])", line):
                line = re.sub(r"^s\s+", "• ", line, count=1)
            compact.append(line)
            previous_blank = False
            continue
        if not previous_blank:
            compact.append("")
        previous_blank = True
    return "\n".join(compact).strip()


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


def _strip_trailing_orphan_bullet(value: str) -> str:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return ""
    return re.sub(r"\s+[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+$", "", normalized).strip()


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
    title = _normalize_requirement_text(str(getattr(card, "subject", None) or card.requirement or ""))
    sub_subject = _normalize_requirement_text(str(getattr(card, "sub_subject", None) or ""))
    title_text = " ".join(part for part in [title, sub_subject] if part).strip()
    if any(keyword in title_text for keyword in ("서식", "별첨")):
        return True, "title_keyword_excluded"
    return False, ""


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
    if max_cols != 4:
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
        score = numbering_like / max(len(non_empty), 1)
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
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []

    # OCR 결과가 "제 3 자", "제 3 자의"처럼 잘못 띄어지는 경우가 많다.
    # 이 상태로 두면 "3"을 목록 번호 마커로 오인해서 상세요건이 비정상 분리된다.
    normalized = re.sub(r"제\s+(\d+)\s+자", r"제\1자", normalized)

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
        r"(?:[①-⑳㉠-㉾❶-❿]+|[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+|"
        r"\(?\d+(?:\.\d+)*[\)\.\-]|"
        r"(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|"
        r"[IVXLCDM]+[\)\.\-])"
    )
    inline_split_pattern = re.compile(
        r"(?:(?<=^)|(?<=\s))(?P<marker>" + marker_prefix_pattern + r")\s+",
        flags=re.IGNORECASE,
    )

    def split_inline_markers(line: str) -> list[str]:
        text = _normalize_requirement_text(line)
        if not text:
            return []
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
        re.match(rf"^{marker_prefix_pattern}\s+", line, flags=re.IGNORECASE)
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
                rf"^(?P<prefix>{marker_prefix_pattern})\s+(?P<body>.+)$",
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
    if re.match(rf"^\s*{marker_prefix_pattern}\s+", single, flags=re.IGNORECASE):
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
        r"(?:(?<=^)|(?<=\s)|(?<=\n))(?:[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
        flags=re.IGNORECASE,
    )
    return bool(bullet_pattern.search(normalized))


def _split_body_detail_units_max_two_sentences(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []

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
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []

    initial_units = _split_atomic_detail_units(normalized) or [normalized]
    final_units: list[str] = []
    marker_pattern = re.compile(
        r"^(?P<prefix>(?:[①-⑳㉠-㉾❶-❿]+|[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-]))\s+(?P<body>.+)$",
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


def _split_three_col_detail_units(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []

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


def _plain_text_from_html_excerpt(html_excerpt: str) -> str:
    if not html_excerpt:
        return ""
    soup = BeautifulSoup(html_excerpt, "html.parser")
    return _normalize_requirement_text(soup.get_text("\n", strip=False))


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
    return leading_texts[-1]


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
    def _strip_leading_numbering(text: str) -> str:
        return re.sub(
            r"^\s*(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+[\.\)]?)\s*",
            "",
            _normalize_requirement_text(text),
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

    def _finalize_item_name(text: str) -> str:
        normalized = _strip_leading_numbering(text)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"\s+관련$", "", normalized).strip()
        normalized = re.sub(r"\s+(?:요청\s*사항|요구사항|요건|통제\s*요구사항)$", "", normalized).strip()
        return normalized

    context = _strip_leading_numbering(context_text)
    title = _strip_leading_numbering(fallback_title)
    candidates = [context, title]
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
            lambda m: _normalize_requirement_text(m.group(1)),
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
                resolved = _finalize_item_name(resolver(match))
                resolved = _preserve_security_domain(text, resolved)
                if resolved:
                    return resolved
    return _preserve_security_domain(context or title, _finalize_item_name(context or title))


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


def _is_auxiliary_third_column_header(header_row: list[str]) -> bool:
    if len(header_row) < 3:
        return False
    third = _normalize_requirement_text(str(header_row[2] or ""))
    if not third:
        return False
    compact_third = re.sub(r"\s+", "", third)
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
    ]
    compact_patterns = [re.sub(r"\s+", "", pattern) for pattern in patterns]
    return any(pattern in compact_third for pattern in compact_patterns)


def _is_numeric_like_auxiliary_value(value: str) -> bool:
    compact = _normalize_requirement_text(value)
    if not compact:
        return False
    if re.search(r"[A-Za-z가-힣]", compact):
        return False
    return bool(re.fullmatch(r"[\d,\.\-/()+%×xX\s]+", compact))


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
    compact_fourth = re.sub(r"\s+", "", fourth)
    patterns = [
        "비고",
        "비고사항",
        "참고사항",
        "참고",
        "추가정보",
        "비고란",
    ]
    compact_patterns = [re.sub(r"\s+", "", pattern) for pattern in patterns]
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
    for table in tables:
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
        str(top_table_context or leading_context or card.requirement or getattr(card, "subject", None) or ""),
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


def _split_inline_heading_and_body(text: str) -> tuple[str, list[str]]:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return "", []

    special_heading_match = re.match(
        r"^(?P<heading>※\s*[^\.。\n]+?)\s+(?P<body>(?:본 사업은|해당 사업은|구축 완료 이후|추후 |당사는 ).+)$",
        normalized,
    )
    if special_heading_match:
        return (
            _normalize_requirement_text(special_heading_match.group("heading")),
            [_normalize_requirement_text(special_heading_match.group("body"))],
        )

    inline_heading_markers = [
        "본 사업은",
        "해당 사업은",
        "구축 완료 이후",
        "추후 ",
        "당사는 ",
    ]
    for marker in inline_heading_markers:
        idx = normalized.find(marker)
        if idx > 0:
            heading = normalized[:idx].strip()
            body = normalized[idx:].strip()
            if heading and body:
                return heading, [body]
    return normalized, []


def _split_body_heading_and_detail_lines(text: str) -> tuple[str, list[str]]:
    normalized = _normalize_requirement_text(text)
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
        inline_markers = (
            "제안사는 ",
            "수행사는 ",
            "납품 솔루션은 ",
            "본 사업은",
            "해당 사업은",
            "구축 완료 이후",
            "추후 ",
            "당사는 ",
        )
        marker_matches = [
            rest.find(marker)
            for marker in inline_markers
            if rest.find(marker) > 0
        ]
        if marker_matches:
            split_idx = min(marker_matches)
            heading_rest = _normalize_requirement_text(rest[:split_idx])
            body = _normalize_requirement_text(rest[split_idx:])
            if prefix and heading_rest and body:
                return f"{prefix} {heading_rest}".strip(), [body]

    if normalized.startswith("※"):
        marker_matches = [
            match.start()
            for marker in ("본 사업은", "해당 사업은", "구축 완료 이후", "추후 ", "당사는 ")
            for match in [re.search(re.escape(marker), normalized)]
            if match and match.start() > 0
        ]
        if marker_matches:
            split_idx = min(marker_matches)
            heading = _normalize_requirement_text(normalized[:split_idx])
            body = _normalize_requirement_text(normalized[split_idx:])
            if heading and body:
                return heading, [body]

    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return "", []
    if len(lines) >= 2:
        return lines[0], lines[1:]

    heading, inline_details = _split_inline_heading_and_body(lines[0])
    return heading, inline_details


def _split_inline_korean_letter_requirements(lines: list[str]) -> list[str]:
    expanded: list[str] = []
    korean_letter_marker = r"(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)"
    pattern = re.compile(rf"(?:(?<=^)|(?<=\s))({korean_letter_marker})[\.\)]\s+")
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if not normalized:
            continue
        matches = list(pattern.finditer(normalized))
        if len(matches) <= 1:
            expanded.append(normalized)
            continue
        chunks = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
            chunks.append(_normalize_requirement_text(normalized[start:end]))
        if (
            len(chunks) >= 2
            and all(re.fullmatch(rf"{korean_letter_marker}[\.\)]", chunk or "") for chunk in chunks[:-1])
            and not re.fullmatch(rf"{korean_letter_marker}[\.\)]", chunks[-1] or "")
        ):
            expanded.append(normalized)
            continue
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
            chunk = _normalize_requirement_text(normalized[start:end])
            if chunk:
                expanded.append(chunk)
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
    if re.match(r"^[○●◦□Oo]\s+", normalized):
        return 1
    if re.match(r"^※\s*", normalized):
        return 2
    if re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s+", normalized, flags=re.IGNORECASE):
        return 1
    if re.match(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)[\.\)]\s+", normalized):
        return 2
    if re.match(r"^(?:\(?\d+\)|\d+[\.\)])\s+", normalized):
        return 3
    if re.match(r"^[\-*·ㆍ]+\s*", normalized):
        return 3
    if re.match(r"^[•▪■◆▶◇□]+\s*", normalized):
        return 2
    return None


def _is_numbered_body_hierarchy_line(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    return bool(
        re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s+", normalized, flags=re.IGNORECASE)
        or re.match(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)[\.\)]\s+", normalized)
        or re.match(r"^(?:\(?\d+\)|\d+[\.\)])\s+", normalized)
    )


def _extract_hierarchical_body_rows(plain_text: str, title: str = "", default_item_name: str = "") -> list[dict]:
    normalized = _normalize_requirement_text(plain_text)
    if not normalized:
        return []

    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 2:
        return []
    lines = _split_inline_korean_letter_requirements(lines)

    normalized_title = _normalize_requirement_text(title)
    work_lines = lines[:]
    if normalized_title and work_lines and _normalize_requirement_text(work_lines[0]) == normalized_title:
        work_lines = work_lines[1:]
    if len(work_lines) < 1:
        return []

    default_level1 = _normalize_requirement_text(default_item_name) or normalized_title or _normalize_requirement_text(lines[0])
    if not default_level1:
        return []

    # If the body contains no numbered hierarchy at all, treat every remaining
    # line as an independent detail item under the card title.
    if not any(_is_numbered_body_hierarchy_line(line) for line in work_lines):
        rows: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        base_requirement = normalized_title or default_level1
        for line in work_lines:
            detail = _normalize_requirement_text(line)
            if not detail:
                continue
            key = (default_level1, base_requirement, detail)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "item_name": default_level1,
                    "requirement": base_requirement,
                    "detail_requirement": detail,
                    "result_note": "",
                    "build_method": "룰 기반(본문 계층 정규화)",
                }
            )
        return rows

    roots: list[dict] = []
    stack: list[dict] = []
    last_node: dict | None = None
    used_levels: set[int] = set()
    intro_lines: list[str] = []

    for line in work_lines:
        raw_level = _body_line_raw_level(line)
        if raw_level is None:
            if last_node is not None:
                last_node["text"] = f"{last_node['text']}\n{line}".strip()
            else:
                intro_lines.append(line)
            continue

        node = {"level": raw_level, "text": line, "children": []}
        used_levels.add(raw_level)
        while stack and int(stack[-1]["level"]) >= raw_level:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)
        stack.append(node)
        last_node = node

    if not roots or not used_levels:
        return []

    compressed_levels = {level: idx + 1 for idx, level in enumerate(sorted(used_levels))}

    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    max_rank = max(compressed_levels.values())

    if intro_lines and max_rank != 2:
        if roots:
            roots[0]["text"] = "\n".join(intro_lines + [str(roots[0]["text"])])
        else:
            return []

    def emit_row(item_name: str, requirement: str, detail_requirement: str, split_long_detail: bool = False) -> None:
        normalized_item = _normalize_requirement_text(item_name)
        normalized_req = _normalize_requirement_text(requirement)
        normalized_detail = _normalize_requirement_text(detail_requirement)
        if not normalized_item or not normalized_req or not normalized_detail:
            return
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
        detail_units = (
            _split_body_detail_units_max_two_sentences(normalized_detail)
            if split_long_detail
            else (_split_atomic_detail_units(normalized_detail) or [normalized_detail])
        )
        for detail_unit in detail_units:
            key = (normalized_item, normalized_req, detail_unit)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "item_name": normalized_item,
                    "requirement": normalized_req,
                    "detail_requirement": detail_unit,
                    "result_note": "",
                    "build_method": "룰 기반(본문 계층 정규화)",
                }
            )

    def walk(node: dict, lineage: list[str]) -> None:
        rank = compressed_levels[int(node["level"])]
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
            if not children:
                if normalized_intro_detail_lines:
                    emit_row(
                        normalized_heading,
                        normalized_heading,
                        "\n".join(normalized_intro_detail_lines),
                        split_long_detail=True,
                    )
                else:
                    emit_row(default_level1, normalized_title or default_level1, text)
                return
            if normalized_intro_detail_lines:
                emit_row(
                    normalized_heading,
                    normalized_heading,
                    "\n".join(normalized_intro_detail_lines),
                    split_long_detail=True,
                )
            for child in children:
                walk(child, [normalized_heading])
            return

        if rank == 2:
            parent_item_name = lineage[0] if lineage else default_level1
            if not children:
                parent_requirement = lineage[-1] if lineage else (normalized_title or default_level1)
                emit_row(parent_item_name, parent_requirement, text)
                return
            for child in children:
                walk(child, [parent_item_name, text])
            return

        item_name = lineage[0] if lineage else default_level1
        requirement = lineage[1] if len(lineage) > 1 else (normalized_title or default_level1)
        emit_row(item_name, requirement, text, split_long_detail=True)

    if max_rank == 1:
        for root in roots:
            emit_row(default_level1, normalized_title or default_level1, _normalize_requirement_text(str(root["text"])))
        return rows

    if max_rank == 2:
        pending_intro_details = [_normalize_requirement_text(line) for line in intro_lines if _normalize_requirement_text(line)]
        for root_idx, root in enumerate(roots):
            root_text = _normalize_requirement_text(str(root.get("text") or ""))
            children = [child for child in list(root.get("children") or []) if _normalize_requirement_text(str(child.get("text") or ""))]
            if not root_text:
                continue
            root_is_shared_parent = re.match(r"^[○●◦□Oo]\s+", root_text) is not None and bool(children)
            if root_is_shared_parent:
                parent_item_name, parent_intro_lines = _split_body_heading_and_detail_lines(root_text)
                parent_item_name = _normalize_requirement_text(parent_item_name or root_text)
                if pending_intro_details:
                    joined_intro_details = "\n".join(pending_intro_details)
                    emit_row(parent_item_name, parent_item_name, joined_intro_details, split_long_detail=True)
                    pending_intro_details = []
                if parent_intro_lines:
                    parent_intro_text = "\n".join(
                        _normalize_requirement_text(line)
                        for line in parent_intro_lines
                        if _normalize_requirement_text(line)
                    )
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
        return rows

    for root in roots:
        walk(root, [])
    return rows


def _extract_shared_parent_lettered_body_rows(plain_text: str, title: str = "") -> list[dict]:
    normalized = _normalize_requirement_text(plain_text)
    if not normalized:
        return []

    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 3:
        return []
    lines = _split_inline_korean_letter_requirements(lines)

    normalized_title = _normalize_requirement_text(title)
    work_lines = lines[:]
    if normalized_title and work_lines and _normalize_requirement_text(work_lines[0]) == normalized_title:
        work_lines = work_lines[1:]
    if len(work_lines) < 2:
        return []
    rows: list[dict] = []
    bullet_pattern = re.compile(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+\s*")

    def append_section_rows(item_name: str, section_lines: list[str], minimum_requirements: int) -> None:
        if not item_name or len(section_lines) < 1:
            return

        requirement_hits: list[tuple[int, str, str]] = []
        for idx, line in enumerate(section_lines):
            req_match = re.match(
                r"^(?P<label>(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하))[\.\)]\s*(?P<rest>.*)$",
                line,
            )
            if req_match:
                requirement_hits.append((idx, f"{req_match.group('label')}.", req_match.group("rest").strip()))

        if len(requirement_hits) < minimum_requirements:
            return

        intro_lines = section_lines[:requirement_hits[0][0]]
        if intro_lines:
            intro_requirement, inline_intro_detail_lines = _split_inline_heading_and_body(intro_lines[0])
            intro_detail_lines = inline_intro_detail_lines + [
                _normalize_requirement_text(line)
                for line in intro_lines[1:]
                if _normalize_requirement_text(line)
            ]
            intro_detail_units: list[str] = []
            if intro_detail_lines:
                joined_intro = "\n".join(intro_detail_lines)
                intro_detail_units = _split_atomic_detail_units(joined_intro) or intro_detail_lines
            else:
                intro_detail_units = [intro_requirement]

            for unit in intro_detail_units:
                rows.append(
                    {
                        "item_name": item_name,
                        "requirement": intro_requirement,
                        "detail_requirement": unit,
                        "result_note": "",
                        "build_method": "LLM+구조정규화",
                    }
                )

        for hit_idx, (line_idx, requirement_label, first_rest) in enumerate(requirement_hits):
            next_idx = requirement_hits[hit_idx + 1][0] if hit_idx + 1 < len(requirement_hits) else len(section_lines)
            requirement_text = f"{requirement_label} {first_rest}".strip() if first_rest else requirement_label
            block_lines = section_lines[line_idx + 1:next_idx]

            detail_units: list[str] = []
            current_unit = ""
            for block_line in block_lines:
                stripped = block_line.strip()
                if not stripped:
                    continue
                if bullet_pattern.match(stripped):
                    if current_unit:
                        detail_units.append(current_unit.strip())
                    current_unit = stripped
                    continue
                if current_unit:
                    current_unit = f"{current_unit} {stripped}".strip()
                else:
                    current_unit = stripped
            if current_unit:
                detail_units.append(current_unit.strip())

            for unit in detail_units:
                rows.append(
                    {
                        "item_name": item_name,
                        "requirement": requirement_text,
                        "detail_requirement": unit,
                        "result_note": "",
                        "build_method": "LLM+구조정규화",
                    }
                )

    parent_sections: list[tuple[str, int, int]] = []
    for idx, line in enumerate(work_lines):
        parent_match = re.match(r"^[○●◦•□▪■◆▶Oo]\s*(.+)$", line)
        if parent_match:
            parent_sections.append((_normalize_requirement_text(line), idx, idx + 1))

    if parent_sections:
        for sec_idx, (item_name, parent_idx, body_start_idx) in enumerate(parent_sections):
            next_parent_idx = parent_sections[sec_idx + 1][1] if sec_idx + 1 < len(parent_sections) else len(work_lines)
            section_lines = work_lines[body_start_idx:next_parent_idx]
            append_section_rows(item_name, section_lines, minimum_requirements=1)
        return rows

    if not normalized_title:
        return []

    append_section_rows(normalized_title, work_lines, minimum_requirements=1)

    return rows


def _section_requirement_prefix(section_name: str) -> str:
    compact = re.sub(r"\s+", " ", (section_name or "")).strip()
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇\(\)\[\]]+\s*", "", compact)
    compact = re.sub(r"^(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+[\.\)]?)\s*", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"[\s/]+", "", compact)
    compact = re.sub(r"[^0-9A-Za-z가-힣]+", "", compact)
    return (compact or "요구사항")[:12]


def _clean_item_name_for_id(item_name: str) -> str:
    cleaned = _normalize_requirement_text(item_name)
    cleaned = re.sub(r"^[○●◦•□▪■◆▶Oo]+\s*", "", cleaned).strip()
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

    compact = re.sub(r"\s+", "", cleaned_item_name)
    compact = re.sub(r"[^0-9A-Za-z가-힣/]+", "", compact)
    return (compact or fallback_prefix)[:16]


def _needs_llm_requirement_id_prefix(item_name: str) -> bool:
    cleaned = _clean_item_name_for_id(item_name)
    if not cleaned:
        return False
    if len(cleaned) >= 18:
        return True
    sentence_markers = [
        "본 사업",
        "본시스템",
        "시스템",
        "구축",
        "제공",
        "지원",
        "필요",
        "위한",
        "통한",
        "관련",
        "대한",
        "적용",
        "구성",
        "관리",
    ]
    return any(marker in cleaned for marker in sentence_markers) and len(cleaned.split()) >= 2


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
        group_name = str(getattr(card, "group", "") or "").strip()
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


def _extract_rows_from_table_card(card: RfpCard, two_col_item_name_override: str = "") -> list[dict]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if "<table" not in html_excerpt.lower():
        return []

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    extracted_rows: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    leading_context = _leading_body_context_from_table_html(html_excerpt, card_title)

    for table in tables:
        matrix = _table_visual_matrix(table, preserve_breaks=True)
        if not matrix:
            continue
        top_table_context, top_context_skip_rows = _top_table_context_from_matrix(matrix, card_title)
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
        if max_cols_before_drop == 4:
            numbering_source_matrix = [list(row) for row in matrix]
            matrix, dropped_numbering_col = _drop_numbering_column_from_matrix(matrix)
        max_cols = max(len(row) for row in matrix)
        if max_cols not in {2, 3, 4}:
            continue

        first_tr = table.find("tr")
        has_header_row = bool(first_tr and first_tr.find("th"))
        header_row = [str(cell or "").strip() for cell in matrix[0]]
        numeric_aux_third = max_cols == 3 and _is_numeric_auxiliary_third_column(matrix)
        is_two_level_three_col = max_cols == 3 and (
            _is_auxiliary_third_column_header(header_row) or numeric_aux_third
        )
        is_three_level_four_col_with_aux_last = max_cols == 4 and _is_auxiliary_fourth_column_header(header_row)
        is_grouped_three_col = max_cols == 3 and _is_grouped_three_level_header(header_row)
        embedded_first_data_row: list[str] | None = None
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

        for row_idx, row in enumerate(data_rows):
            padded = list(row) + [""] * max(0, max_cols - len(row))
            original_row_signature = [str(cell or "").strip() for cell in padded[:max_cols]]
            if original_row_signature == header_row:
                continue
            numbered_row = any(_has_leading_numbering(str(cell or "")) for cell in padded[:max_cols])
            result_note = ""
            dropped_numbering_value = ""
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
                    str(top_table_context or leading_context or card.requirement or getattr(card, "subject", None) or ""),
                    card_title,
                )
                requirement = _normalize_requirement_text(str(padded[0] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[1] or ""))
            elif is_two_level_three_col:
                item_name = _normalize_requirement_text(str(two_col_item_name_override or "")) or _normalize_two_col_table_item_name(
                    str(top_table_context or leading_context or card.requirement or getattr(card, "subject", None) or ""),
                    card_title,
                )
                requirement = _normalize_requirement_text(str(padded[0] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[1] or ""))
                result_note = _normalize_requirement_text(str(padded[2] or ""))
            elif max_cols == 3:
                item_name = _normalize_three_col_table_item_name(
                    str(padded[0] or ""),
                    str(top_table_context or leading_context or card.requirement or getattr(card, "subject", None) or ""),
                    card_title,
                )
                requirement = _normalize_requirement_text(str(padded[1] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[2] or ""))
            elif is_three_level_four_col_with_aux_last:
                item_name = _normalize_three_col_table_item_name(
                    str(padded[0] or ""),
                    str(top_table_context or leading_context or card.requirement or getattr(card, "subject", None) or ""),
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

            if not item_name and not requirement and not detail_requirement:
                continue
            row_signature = (
                [requirement, detail_requirement, result_note]
                if is_two_level_three_col
                else ([item_name, requirement, detail_requirement] if max_cols == 3
                else ([result_note, item_name, requirement, detail_requirement] if max_cols == 4 else [requirement, detail_requirement])
                )
            )
            if row_signature == header_row:
                continue
            if not detail_requirement:
                continue
            if not requirement:
                requirement = str(getattr(card, "subject", None) or card.requirement or "").strip()
            if not item_name and is_two_level_three_col:
                item_name = _normalize_two_col_table_item_name(
                    str(top_table_context or leading_context or card.requirement or getattr(card, "subject", None) or ""),
                    card_title,
                )
            if not item_name and max_cols == 3:
                item_name = str(getattr(card, "sub_subject", None) or getattr(card, "subject", None) or card.requirement or "").strip()
            if not item_name and max_cols == 4:
                item_name = str(leading_context or getattr(card, "sub_subject", None) or getattr(card, "subject", None) or card.requirement or "").strip()

            if _is_header_like_field(requirement) and detail_requirement:
                if max_cols == 2 or is_two_level_three_col:
                    contextual_item_name = _normalize_two_col_table_item_name(
                        str(top_table_context or leading_context or card.requirement or getattr(card, "subject", None) or ""),
                        card_title,
                    )
                    if contextual_item_name:
                        item_name = contextual_item_name
                    promoted_requirement = _normalize_requirement_text(str(padded[0] or ""))
                    if promoted_requirement and not _is_header_like_field(promoted_requirement):
                        requirement = promoted_requirement
                elif max_cols == 3:
                    contextual_item_name = _normalize_two_col_table_item_name(
                        str(top_table_context or leading_context or card.requirement or getattr(card, "subject", None) or ""),
                        card_title,
                    )
                    if contextual_item_name:
                        item_name = contextual_item_name
                    promoted_requirement = _normalize_requirement_text(str(padded[0] or ""))
                    if promoted_requirement and not _is_header_like_field(promoted_requirement):
                        requirement = promoted_requirement
                elif is_three_level_four_col_with_aux_last:
                    contextual_item_name = _normalize_two_col_table_item_name(
                        str(top_table_context or leading_context or card.requirement or getattr(card, "subject", None) or ""),
                        card_title,
                    )
                    if contextual_item_name:
                        item_name = contextual_item_name
                    promoted_requirement = _normalize_requirement_text(str(padded[0] or ""))
                    if promoted_requirement and not _is_header_like_field(promoted_requirement):
                        requirement = promoted_requirement
                elif max_cols == 4:
                    contextual_item_name = str(leading_context or getattr(card, "sub_subject", None) or getattr(card, "subject", None) or card.requirement or "").strip()
                    if contextual_item_name:
                        item_name = contextual_item_name
                    promoted_requirement = _normalize_requirement_text(str(padded[1] or ""))
                    if promoted_requirement and not _is_header_like_field(promoted_requirement):
                        requirement = promoted_requirement

            if max_cols == 2 or is_two_level_three_col:
                detail_units = _split_detail_units_one_sentence(detail_requirement)
            elif max_cols == 3:
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
                        "build_method": (
                            "룰 기반(4단 표->3단 표+추가정보)"
                            if is_three_level_four_col_with_aux_last
                            else (
                            "룰 기반(4단 표)"
                            if max_cols == 4
                            else (
                                "룰 기반(3단 표->2단 표+추가정보)"
                                if is_two_level_three_col
                                else (
                                "룰 기반(3단 표-그룹헤더)"
                                if is_grouped_three_col
                                else (
                                "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)"
                                if dropped_numbering_col is not None and max_cols == 3
                                else (
                                    "룰 기반(넘버링 표->3단 처리)"
                                    if max_cols == 2 and numbered_row
                                    else ("룰 기반(2단 표)" if max_cols == 2 else "룰 기반(3단 표)")
                                )
                                )
                                )
                            )
                            )
                        ),
                    }
                )

    return extracted_rows


def _section_context_fallback(section_name: str, cards: list[RfpCard]) -> dict:
    first_card = cards[0] if cards else None
    return {
        "section_title": section_name,
        "id_prefix": _section_requirement_prefix(section_name),
        "default_item_name": str(getattr(first_card, "group", "") or section_name).strip() or section_name,
        "section_summary": "",
        "hierarchy_guidance": "",
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
                "html_excerpt": str(card.html_excerpt or "")[:8000],
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
                    "Use html_excerpt to understand hierarchy, list nesting, heading emphasis, and table/list boundaries. "
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
    plain_text = _plain_text_from_html_excerpt(str(card.html_excerpt or ""))
    lines = [line.strip(" -\u2022\t") for line in plain_text.split("\n") if line.strip()]
    title = str(getattr(card, "subject", None) or card.requirement or "").strip() or str(card.requirement)
    item_name = str(getattr(card, "sub_subject", None) or section_context.get("default_item_name") or section_context.get("section_title") or title).strip()
    content_lines = [line for line in lines if line != title]
    if not content_lines and plain_text:
        content_lines = [re.sub(r"\s+", " ", plain_text).strip()]
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


def _build_card_requirement_rows_via_llm(client, card: RfpCard, section_context: dict, model_name: str) -> tuple[list[dict], dict, str]:
    zero_usage = {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    html_excerpt = str(card.html_excerpt or "")
    has_table_card = "<table" in html_excerpt.lower()

    rule_based_table_rows = _extract_rows_from_table_card(card)
    if rule_based_table_rows:
        return (
            rule_based_table_rows,
            zero_usage,
            "RULE_BASED_TABLE",
        )

    pre_usage = zero_usage
    if has_table_card:
        try:
            two_col_item_name_override, pre_usage = _infer_two_col_table_item_name_via_llm(client, card, section_context, model_name)
        except Exception:
            two_col_item_name_override = ""
            pre_usage = zero_usage
        if two_col_item_name_override:
            rule_based_table_rows = _extract_rows_from_table_card(card, two_col_item_name_override=two_col_item_name_override)
            if rule_based_table_rows:
                return (
                    rule_based_table_rows,
                    pre_usage,
                    "RULE_BASED_TABLE_WITH_LLM_ITEM_NAME",
                )

    plain_text = _plain_text_from_html_excerpt(html_excerpt)
    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    normalized_rows = _extract_hierarchical_body_rows(
        plain_text,
        card_title,
        str(card_title or getattr(card, "sub_subject", None) or section_context.get("default_item_name") or section_context.get("section_title") or ""),
    )
    if not normalized_rows:
        normalized_rows = _extract_shared_parent_lettered_body_rows(plain_text, card_title)
    if normalized_rows and not has_table_card:
        return normalized_rows, zero_usage, "RULE_BASED_BODY"
    if not has_table_card and _should_skip_llm_for_heading_only_body_card(plain_text, card_title):
        return [], zero_usage, "SKIPPED_HEADING_ONLY_BODY"

    payload = {
        "section_context": section_context,
        "card": {
            "card_no": getattr(card, "card_no", None) or card.card_id,
            "requirement": card.requirement,
            "subject": getattr(card, "subject", None),
            "sub_subject": getattr(card, "sub_subject", None),
            "group": card.group,
            "section": card.section,
            "has_table": has_table_card,
            "plain_text": plain_text[:12000],
            "html_excerpt": html_excerpt[:16000],
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
                    "Use html_excerpt as the primary source for hierarchy, nesting, list grouping, heading emphasis, and structural boundaries. "
                    "Use plain_text as a secondary source to preserve exact wording. "
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
                    "If one detailed line contains chained inline markers like '가. 나. 다. 라. ...', do not drop any of those markers; keep the full original marker chain in the resulting detail_requirement text. "
                    "For body content, first infer a clean hierarchy of 1-level, 2-level, or 3-level from the source structure. "
                    "Then map it strictly as follows: 1-level body => detail_requirement only; 2-level body => requirement + detail_requirement; 3-level body => item_name + requirement + detail_requirement. "
                    "When the body has only 1 visible level, use the card title or section context for the upper fields and place the actual body line in detail_requirement. "
                    "When the body has 2 visible levels, use the level-1 line as requirement and the level-2 line as detail_requirement. "
                    "When the body has 3 visible levels, use level-1 as item_name, level-2 as requirement, and level-3 as detail_requirement. "
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
            if not item_name or not requirement or not detail_requirement:
                continue
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
    if not rows:
        rows = _fallback_card_requirement_rows(card, section_context)
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
    note_text = _plain_text_from_html_excerpt(note_html)
    if not note_text.strip():
        return [], zero_usage, "FOLLOWUP_NOTE_EMPTY"

    previous_soup = BeautifulSoup(previous_html, "html.parser")
    previous_tables = previous_soup.find_all("table")
    target_table = previous_tables[-1] if previous_tables else None
    target_matrix = _table_visual_matrix(target_table) if target_table is not None else []
    card_title = str(getattr(previous_table_card, "subject", None) or previous_table_card.requirement or "").strip()
    leading_context = _leading_body_context_from_table_html(previous_html, card_title)
    top_table_context, _ = _top_table_context_from_matrix(target_matrix, card_title)

    first_column_preview: list[str] = []
    for row in target_matrix[1:7] if len(target_matrix) > 1 else target_matrix[:6]:
        first_value = _normalize_requirement_text(str((row[0] if row else "") or ""))
        if first_value and first_value not in first_column_preview:
            first_column_preview.append(first_value)

    payload = {
        "section_context": {
            "section_title": str(section_context.get("section_title") or ""),
            "default_item_name": str(section_context.get("default_item_name") or ""),
            "id_prefix": str(section_context.get("id_prefix") or ""),
        },
        "previous_table_card": {
            "card_no": getattr(previous_table_card, "card_no", None) or previous_table_card.card_id,
            "subject": str(getattr(previous_table_card, "subject", None) or previous_table_card.requirement or ""),
            "requirement": str(previous_table_card.requirement or ""),
            "leading_context": leading_context,
            "top_table_context": top_table_context,
            "table_header": target_matrix[0] if target_matrix else [],
            "first_column_preview": first_column_preview,
            "table_rows_preview": target_matrix[:6],
            "html_excerpt": previous_html[:12000],
        },
        "note_card": {
            "card_no": getattr(note_card, "card_no", None) or note_card.card_id,
            "subject": str(getattr(note_card, "subject", None) or note_card.requirement or ""),
            "requirement": str(note_card.requirement or ""),
            "plain_text": note_text[:4000],
            "html_excerpt": note_html[:4000],
        },
    }

    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You convert a short note that appears immediately after a table into requirement rows using the preceding table context. "
                    "Return exactly one JSON object inside a JSON array. Each item must have keys: item_name, requirement, detail_requirement, result_note, id_title_hint. "
                    "Treat the note as a table-wide common note unless the note explicitly refers to one specific row only. "
                    "Use the preceding table's leading_context, top_table_context, table header, first-column preview, and html_excerpt to infer the correct hierarchy. "
                    "Do not output generic placeholders such as 'requirement', '상세 요구사항', 'table', or a raw header label unless the source explicitly uses that as the true heading. "
                    "Do not anchor this note to only the last table row unless the note explicitly mentions that row. "
                    "detail_requirement must preserve the note text verbatim. "
                    "requirement must be a short heading-like label inferred from the broader table context. "
                    "item_name must be the most specific upper category supported by the surrounding table context. "
                    "id_title_hint must be a broader table-level heading suitable for requirement ID generation, not the last row label. "
                    "All output values must be plain text only. Do not invent content."
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
        for item in parsed[:1]:
            if not isinstance(item, dict):
                continue
            item_name = _normalize_requirement_text(str(item.get("item_name") or "").strip())
            requirement = _normalize_requirement_text(str(item.get("requirement") or "").strip())
            detail_requirement = _normalize_requirement_text(str(item.get("detail_requirement") or "").strip())
            if not item_name or not requirement or not detail_requirement:
                continue
            rows.append(
                {
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": detail_requirement,
                    "result_note": _normalize_requirement_text(str(item.get("result_note") or "").strip()),
                    "id_title_hint": _normalize_requirement_text(str(item.get("id_title_hint") or "").strip()),
                    "build_method": "LLM(표후속공통주석)",
                }
            )

    if not rows:
        fallback_heading = _normalize_requirement_text(
            str(top_table_context or leading_context or previous_table_card.requirement or note_card.requirement or "")
        )
        fallback_item_name = _normalize_requirement_text(
            str(section_context.get("default_item_name") or fallback_heading or note_card.requirement or "")
        )
        rows = [
            {
                "item_name": fallback_item_name or fallback_heading or "공통",
                "requirement": fallback_heading or fallback_item_name or "공통",
                "detail_requirement": _normalize_requirement_text(note_text),
                "result_note": "",
                "id_title_hint": fallback_heading or fallback_item_name,
                "build_method": "룰 기반(표후속공통주석 fallback)",
            }
        ]
    else:
        rows = rows[:1]

    input_tokens, output_tokens = _extract_token_usage(response)
    usage = {
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, input_tokens, output_tokens),
    }
    return rows, usage, raw_content


def _build_section_requirement_tables(cards: list[RfpCard], model_name: str) -> tuple[dict[str, list[dict]], list[dict], dict]:
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

    for section_index, (section_name, section_cards) in enumerate(grouped_cards, start=1):
        progress.progress(processed_cards / total_cards, text=f"섹션 컨텍스트 생성 중: {section_name}")
        try:
            section_context, section_usage, section_raw = _build_section_context_via_llm(client, section_name, section_cards, model_name)
        except Exception as exc:
            section_context = _section_context_fallback(section_name, section_cards)
            section_usage = {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            section_raw = f"ERROR: {exc}"
        total_input_tokens += int(section_usage.get("input_tokens", 0) or 0)
        total_output_tokens += int(section_usage.get("output_tokens", 0) or 0)

        prefix = _section_requirement_prefix(str(section_context.get("id_prefix") or section_name))
        section_rows: list[dict] = []
        seen_keys: set[tuple[str, str, str, str]] = set()
        previous_section_card: RfpCard | None = None

        for card in section_cards:
            processed_cards += 1
            progress.progress(min(processed_cards / total_cards, 1.0), text=f"카드 분석 중: {section_name} / {getattr(card, 'card_no', card.card_id)}")
            sub_subject = str(getattr(card, "sub_subject", "") or "").strip()
            build_source = "표" if sub_subject.startswith("표") or "<table" in str(card.html_excerpt or "").lower() else "본문"
            is_table_followup_common_note = _is_table_followup_common_note_card(previous_section_card, card)
            inherit_prev_table_prefix = (
                _inherits_requirement_id_from_previous_table(previous_section_card, card)
                and not is_table_followup_common_note
            )
            debug_plain_text = _plain_text_from_html_excerpt(str(card.html_excerpt or ""))
            skip_card, skip_reason = _should_skip_requirement_extraction(card)
            if skip_card:
                debug_rows.append(
                    {
                        "section": section_name,
                        "card_no": getattr(card, "card_no", None) or card.card_id,
                        "subject": getattr(card, "subject", None) or card.requirement,
                        "build_source": build_source,
                        "plain_text": debug_plain_text[:6000],
                        "html_excerpt": str(card.html_excerpt or "")[:12000],
                        "normalized_rows": [],
                        "context_raw": section_raw[:3000],
                        "card_raw": f"SKIPPED: {skip_reason}",
                    }
                )
                previous_section_card = card
                continue
            try:
                if is_table_followup_common_note and previous_section_card is not None:
                    card_rows, card_usage, card_raw = _build_table_followup_note_rows_via_llm(
                        client,
                        previous_section_card,
                        card,
                        section_context,
                        model_name,
                    )
                else:
                    card_rows, card_usage, card_raw = _build_card_requirement_rows_via_llm(client, card, section_context, model_name)
            except Exception as exc:
                card_rows = _fallback_card_requirement_rows(card, section_context)
                card_usage = {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
                card_raw = f"ERROR: {exc}"
            total_input_tokens += int(card_usage.get("input_tokens", 0) or 0)
            total_output_tokens += int(card_usage.get("output_tokens", 0) or 0)

            normalized_row_preview = [
                {
                    "item_name": _normalize_requirement_text(str(item.get("item_name") or "")),
                    "requirement": _normalize_requirement_text(str(item.get("requirement") or "")),
                    "detail_requirement": _normalize_requirement_text(str(item.get("detail_requirement") or "")),
                    "result_note": _normalize_requirement_text(str(item.get("result_note") or "")),
                    "build_method": _normalize_requirement_text(str(item.get("build_method") or "")),
                }
                for item in card_rows
                if isinstance(item, dict)
            ]
            save_preview_rows: list[dict] = []

            debug_rows.append(
                {
                    "section": section_name,
                    "card_no": getattr(card, "card_no", None) or card.card_id,
                    "subject": getattr(card, "subject", None) or card.requirement,
                    "build_source": build_source,
                    "plain_text": debug_plain_text[:6000],
                    "html_excerpt": str(card.html_excerpt or "")[:12000],
                    "normalized_rows": normalized_row_preview,
                    "save_preview_rows": save_preview_rows,
                    "context_raw": section_raw[:3000],
                    "card_raw": card_raw[:6000],
                }
            )

            for item in card_rows:
                item_name = _strip_trailing_orphan_bullet(_normalize_requirement_text(str(item.get("item_name") or "")))
                requirement = _strip_trailing_orphan_bullet(_normalize_requirement_text(str(item.get("requirement") or "")))
                detail_requirement = _strip_trailing_orphan_bullet(_normalize_requirement_text(str(item.get("detail_requirement") or "")))
                result_note = _strip_trailing_orphan_bullet(_normalize_requirement_text(str(item.get("result_note") or "")))
                build_method = _normalize_requirement_text(str(item.get("build_method") or "")) or (
                    "룰 기반"
                    if str(card_raw).startswith("RULE_BASED")
                    else "LLM"
                )
                dedup_key = (item_name, requirement, detail_requirement, result_note)
                if (
                    not item_name
                    or not requirement
                    or not detail_requirement
                    or _is_header_like_requirement_row(item_name, requirement, detail_requirement, result_note)
                    or _is_redundant_same_text_requirement_row(item_name, requirement, detail_requirement)
                    or dedup_key in seen_keys
                ):
                    continue
                seen_keys.add(dedup_key)
                save_preview_rows.append(
                    {
                        "item_name": item_name,
                        "requirement": requirement,
                        "detail_requirement": detail_requirement,
                        "result_note": result_note,
                        "build_method": build_method,
                    }
                )
                section_rows.append(
                    {
                        "요구사항 ID": "",
                        "항목명": item_name,
                        "요구사항": requirement,
                        "상세요건": detail_requirement,
                        "Category": str(getattr(card, "category", None) or "").strip(),
                        "Section": str(getattr(card, "section", None) or "").strip(),
                        "Part": str(getattr(card, "part", None) or card.group or "").strip(),
                        "추가정보": result_note,
                        "페이지": (card.page_idx + 1) if isinstance(card.page_idx, int) else "",
                        "생성 출처": build_source,
                        "생성 방식": build_method,
                        "_id_title_hint": _normalize_requirement_text(str(item.get("id_title_hint") or "")),
                        "_card_no": str(getattr(card, "card_no", None) or ""),
                        "_inherit_prev_table_prefix": inherit_prev_table_prefix,
                    }
                )
            previous_section_card = card

        prefix_counters: dict[str, int] = {}
        id_prefix_cache: dict[tuple[str, str, str, str], str] = {}
        last_row_prefix = ""
        for row in section_rows:
            inherit_prev_table_prefix = bool(row.get("_inherit_prev_table_prefix"))
            id_source = str(row.get("_id_title_hint") or row.get("항목명") or "")
            category_text = str(row.get("Category") or row.get("카테고리") or "")
            row_prefix = ""
            if inherit_prev_table_prefix and last_row_prefix:
                row_prefix = last_row_prefix
            else:
                row_prefix = _row_requirement_id_prefix(id_source, category_text, section_name, prefix)
            if row_prefix and not (inherit_prev_table_prefix and last_row_prefix) and _needs_llm_requirement_id_prefix(id_source):
                cache_key = (id_source, category_text, section_name, prefix)
                cached_prefix = id_prefix_cache.get(cache_key)
                if cached_prefix:
                    row_prefix = cached_prefix
                else:
                    try:
                        inferred_prefix, prefix_usage = _infer_requirement_id_prefix_via_llm(
                            client,
                            id_source,
                            category_text,
                            section_name,
                            row_prefix,
                            model_name,
                        )
                        total_input_tokens += int(prefix_usage.get("input_tokens", 0) or 0)
                        total_output_tokens += int(prefix_usage.get("output_tokens", 0) or 0)
                        row_prefix = inferred_prefix or row_prefix
                    except Exception:
                        pass
                    id_prefix_cache[cache_key] = row_prefix
            prefix_counters[row_prefix] = prefix_counters.get(row_prefix, 0) + 1
            row["요구사항 ID"] = f"{row_prefix}_{prefix_counters[row_prefix]:03d}"
            last_row_prefix = row_prefix
            row.pop("_id_title_hint", None)
            row.pop("_card_no", None)
            row.pop("_inherit_prev_table_prefix", None)
        if section_rows:
            section_tables[section_name] = section_rows

    progress.progress(1.0, text="섹션단위로 항목정리 완료")
    usage_info = {
        "model": model_name,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, total_input_tokens, total_output_tokens),
    }
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
        render_cards(cards)

        match_debug = st.session_state.get("step3_toc_match_debug") or []
        if match_debug:
            matched_count = sum(1 for row in match_debug if row.get("matched"))
            unmatched_rows = [row for row in match_debug if not row.get("matched")]
            st.markdown("**목차-본문 매칭 디버그**")
            st.caption(
                f"전체 {len(match_debug)}건 | 매칭 {matched_count}건 | 실패 {len(unmatched_rows)}건"
            )
            st.dataframe(
                [
                    {
                        "toc_index": row.get("toc_index"),
                        "level": row.get("level"),
                        "title": row.get("title"),
                        "matched": row.get("matched"),
                        "resolved_index": row.get("resolved_index"),
                        "reason": row.get("reason"),
                        "matched_text": row.get("matched_text"),
                    }
                    for row in match_debug
                ],
                use_container_width=True,
                hide_index=True,
            )
            if unmatched_rows:
                with st.expander("매칭 실패 항목 상세", expanded=False):
                    for row in unmatched_rows:
                        st.markdown(f"**{row.get('title', '')}**")
                        st.caption(
                            f"toc_index={row.get('toc_index')} | level={row.get('level')} | "
                            f"start_from={row.get('start_from')} | reason={row.get('reason')}"
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

        current_signature = _section_requirement_cards_signature(source_cards)
        cached_signature = st.session_state.get("cards_step2_section_requirement_signature")
        if cached_signature != current_signature:
            _clear_section_requirement_cache()

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
        selected_cards: list[RfpCard] = []
        for section_name in resolved_selected_section_names:
            selected_cards.extend(section_name_to_cards.get(section_name, []))
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
                section_tables, debug_rows, usage_info = _build_section_requirement_tables(selected_cards, model_name)
                st.session_state["cards_step2_section_requirement_tables"] = section_tables
                st.session_state["cards_step2_section_requirement_debug"] = debug_rows
                st.session_state["cards_step2_section_requirement_usage"] = usage_info
                st.session_state["cards_step2_section_requirement_signature"] = current_signature
                st.session_state["llm_cost_total_usd"] = float(st.session_state.get("llm_cost_total_usd", 0.0) or 0.0) + float(
                    usage_info.get("cost_usd", 0.0) or 0.0
                )
                st.session_state.setdefault("llm_cost_logs", []).append(usage_info)
                if keep_artifacts and st.session_state.get("file_name"):
                    artifact_root = Path("artifacts") / Path(st.session_state["file_name"]).stem
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
        usage_info = st.session_state.get("cards_step2_section_requirement_usage") or {}
        if section_tables:
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
                    st.dataframe(
                        [
                            {
                                **row,
                                "Part": row.get("Part") or "-",
                                "Section": row.get("Section") or "-",
                                "Category": row.get("Category") or row.get("카테고리") or "-",
                            }
                            for row in rows
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
            debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
            if debug_rows:
                with st.expander("섹션단위로 항목정리 디버그", expanded=False):
                    st.dataframe(
                        [
                            {
                                "section": row.get("section"),
                                "card_no": row.get("card_no"),
                                "subject": row.get("subject"),
                                "build_source": row.get("build_source"),
                                "normalized_row_count": len(row.get("normalized_rows") or []),
                                "plain_text_preview": str(row.get("plain_text") or "")[:200],
                                "html_excerpt_preview": str(row.get("html_excerpt") or "")[:200],
                            }
                            for row in debug_rows
                        ],
                        use_container_width=True,
                        hide_index=True,
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
                            with st.expander("원문/중간 데이터", expanded=False):
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
