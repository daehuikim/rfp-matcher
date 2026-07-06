from __future__ import annotations

import re
from typing import Callable

from bs4 import BeautifulSoup

from rfpmatch.models import RfpCard, Section, TocItem


def _deep_table_list_html(html_snippet: str) -> str:
    if not html_snippet.strip():
        return ""
    soup = BeautifulSoup(html_snippet, "html.parser")
    kept: list[str] = []
    for tag in soup.find_all(["table", "ul", "ol"]):
        kept.append(str(tag))
    # When only list-item fragments are present, keep them as well so lower bullet
    # levels are not dropped in deep extraction.
    if not kept:
        for tag in soup.find_all(["li"]):
            kept.append(str(tag))
    if kept:
        return "\n".join(kept)
    return html_snippet


def _clean_title(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    text = re.sub(r"\s*/\s*Body\s*\d+\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*/\s*Part\s*\d+\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+/+\s*$", "", text)
    return text


def _normalize_title_for_match(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip().lower()
    compact = re.sub(r"^[ivxlcdm]+\b[\.\-\)]?\s+", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"^\d+(?:\.\d+)*[\.\-\)]?\s+", "", compact)
    compact = re.sub(r"^제\s*\d+\s*(장|절|항)\s*", "", compact)
    return compact


def _find_best_toc_title(toc_items: list[TocItem], text: str) -> str:
    if not toc_items:
        return ""
    norm_text = _normalize_title_for_match(text)
    best = ""
    best_len = -1
    for item in toc_items:
        title = _clean_title(item.title)
        norm_title = _normalize_title_for_match(title)
        if not norm_title:
            continue
        if norm_title in norm_text or norm_text in norm_title:
            if len(norm_title) > best_len:
                best = title
                best_len = len(norm_title)
    return best


def _table_title_from_headers(table, fallback: str = "") -> str:
    if not table:
        return _clean_title(fallback or "table")

    header_rows = []
    first_data_row = None
    for tr in table.find_all("tr", recursive=True):
        cells = tr.find_all(["th", "td"], recursive=True)
        if not cells:
            continue
        if tr.find("th"):
            header_rows.append(tr)
        elif first_data_row is None:
            first_data_row = tr

    header_cells: list[str] = []
    if header_rows:
        for tr in header_rows[:2]:
            for cell in tr.find_all(["th", "td"], recursive=True)[:6]:
                text = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
                if text and text not in header_cells:
                    header_cells.append(text)
    if not header_cells and first_data_row is not None:
        for cell in first_data_row.find_all(["td", "th"], recursive=True)[:6]:
            text = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            if text:
                header_cells.append(text)
    if not header_cells:
        return _clean_title(fallback or "table")
    title = " / ".join(header_cells[:3])
    return _clean_title(title)


def _split_table_to_logical_cards(table_html: str) -> list[str]:
    soup = BeautifulSoup(table_html, "html.parser")
    table = soup.find("table")
    if not table:
        return [table_html]

    rows = table.find_all("tr", recursive=True)
    if len(rows) <= 2:
        return [str(table)]

    cards: list[str] = []
    current_rows: list[str] = []
    header_rows: list[str] = []
    for idx, tr in enumerate(rows):
        cells = tr.find_all(["th", "td"], recursive=True)
        row_text = re.sub(r"\s+", " ", tr.get_text(" ", strip=True)).strip()
        if not row_text:
            continue
        if idx == 0 or tr.find("th"):
            header_rows.append(str(tr))
            continue
        if len(cells) >= 3 and current_rows:
            cards.append("<table>" + "".join(header_rows + current_rows) + "</table>")
            current_rows = [str(tr)]
        else:
            current_rows.append(str(tr))
    if current_rows:
        cards.append("<table>" + "".join(header_rows + current_rows) + "</table>")
    return cards or [str(table)]


def _split_section_into_top_level_units(section: Section) -> list[Section]:
    """
    Split a section into top-level renderable units.

    Goal:
    - keep TOC/body heading units as one card when content is mostly prose
    - split out top-level tables as their own cards
    - split large body content around heading/table boundaries so cards stay compact
    """
    html = (section.html or "").strip()
    if not html:
        return [section]

    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    children = [child for child in body.children if getattr(child, "name", None)]

    units: list[tuple[str, str]] = []
    current_parts: list[str] = []

    def flush_current() -> None:
        nonlocal current_parts
        chunk = "\n".join(current_parts).strip()
        if chunk:
            units.append(("body", chunk))
        current_parts = []

    for child in children:
        if child.name in {"table"}:
            flush_current()
            units.append(("table", str(child)))
            continue
        if re.fullmatch(r"h[1-6]", child.name or "") and current_parts:
            flush_current()
            current_parts.append(str(child))
            continue
        current_parts.append(str(child))

    flush_current()

    if len(units) <= 1:
        return [section]

    exploded: list[Section] = []
    table_index = 0
    body_index = 0
    root_table_title = ""
    last_table_title = _clean_title(section.title)
    for kind, chunk in units:
        if not chunk.strip():
            continue
        if kind == "table":
            table_index += 1
            table_soup = BeautifulSoup(chunk, "html.parser")
            table = table_soup.find("table")
            parent_title = root_table_title or last_table_title or section.title
            table_title = _table_title_from_headers(table, fallback=parent_title)
            if table_title and table_title.lower() == "table":
                table_title = parent_title
            if not root_table_title and table_title:
                root_table_title = table_title
            # If this table looks like a continuation table, inherit the original
            # root parent title instead of drifting to the most recent fragment.
            if table_title and parent_title and table_title != parent_title:
                if table_title.lower().startswith(parent_title.lower()) or parent_title.lower().startswith(table_title.lower()):
                    table_title = parent_title
            last_table_title = table_title
            logical_tables = _split_table_to_logical_cards(chunk)
            if len(logical_tables) <= 1:
                table_html = _deep_table_list_html(chunk)
                table_text = BeautifulSoup(table_html, "html.parser").get_text(" ", strip=True)
                exploded.append(
                    Section(
                        title=_clean_title(table_title),
                        anchor=section.anchor,
                        level=min(section.level + 1, 6),
                        page_idx=section.page_idx,
                        html=table_html,
                        text=table_text,
                    )
                )
            else:
                for sub_idx, logical_html in enumerate(logical_tables, start=1):
                    table_html = _deep_table_list_html(logical_html)
                    table_text = BeautifulSoup(table_html, "html.parser").get_text(" ", strip=True)
                    sub_title = _clean_title(table_title if sub_idx == 1 else f"{table_title} / Part {sub_idx}")
                    exploded.append(
                        Section(
                            title=sub_title,
                            anchor=section.anchor,
                            level=min(section.level + 1, 6),
                            page_idx=section.page_idx,
                            html=table_html,
                            text=table_text,
                        )
                    )
        else:
            body_index += 1
            body_text = BeautifulSoup(chunk, "html.parser").get_text(" ", strip=True)
            title = section.title if body_index == 1 else f"{section.title} / Body {body_index}"
            exploded.append(
                Section(
                    title=title,
                    anchor=section.anchor,
                    level=section.level,
                    page_idx=section.page_idx,
                    html=chunk,
                    text=body_text,
                )
            )

    return exploded


def _split_html_by_headings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    tags = body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "table"], recursive=True)

    blocks: list[dict] = []
    current_heading = ""
    current_level = 1
    current_parts: list[str] = []

    for tag in tags:
        # Keep table as a single unit.
        if tag.name != "table" and tag.find_parent("table"):
            continue
        if tag.name == "p" and tag.find_parent("li"):
            continue

        if re.fullmatch(r"h[1-6]", tag.name or ""):
            if current_parts:
                html_block = "\n".join(current_parts).strip()
                text_block = BeautifulSoup(html_block, "html.parser").get_text(" ", strip=True)
                blocks.append(
                    {
                        "heading": current_heading or "(preamble)",
                        "level": current_level,
                        "html": html_block,
                        "text": text_block,
                    }
                )
                current_parts = []
            current_heading = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
            current_level = int(tag.name[1])

        current_parts.append(str(tag))

    if current_parts:
        html_block = "\n".join(current_parts).strip()
        text_block = BeautifulSoup(html_block, "html.parser").get_text(" ", strip=True)
        blocks.append(
            {
                "heading": current_heading or "(preamble)",
                "level": current_level,
                "html": html_block,
                "text": text_block,
            }
        )
    return blocks


def _map_schema_to_heading_blocks(deep_schema_rows: list[dict], blocks: list[dict]) -> list[Section]:
    if not deep_schema_rows or not blocks:
        return []

    def norm(value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip().lower()
        value = re.sub(r"^[ivxlcdm]+\b[\.\-\)]?\s+", "", value, flags=re.IGNORECASE)
        value = re.sub(r"^\d+(?:\.\d+)*[\.\-\)]?\s+", "", value)
        value = re.sub(r"^제\s*\d+\s*(장|절|항)\s*", "", value)
        return value

    starts: list[int] = []
    cursor = 0
    for row in deep_schema_rows:
        hint = norm(str(row.get("start_hint", "")))
        title = norm(str(row.get("card_title", row.get("title", ""))))
        found = -1
        for i in range(cursor, len(blocks)):
            heading = norm(str(blocks[i].get("heading", "")))
            body = norm(str(blocks[i].get("text", "")))
            if hint and (hint in heading or hint in body):
                found = i
                break
            if title and (title in heading or title in body):
                found = i
                break
        starts.append(found)
        if found >= 0:
            cursor = found + 1

    # Fill unmatched rows so slicing is contiguous.
    matched = [(idx, pos) for idx, pos in enumerate(starts) if pos >= 0]
    if not matched:
        starts = [0] * len(deep_schema_rows)
    else:
        first_idx, first_pos = matched[0]
        for i in range(0, first_idx):
            starts[i] = first_pos
        next_it = iter(matched)
        current = next(next_it)
        nxt = next(next_it, None)
        for i in range(len(starts)):
            if starts[i] >= 0:
                current = (i, starts[i])
                if nxt and i >= nxt[0]:
                    nxt = next(next_it, None)
                continue
            starts[i] = nxt[1] if nxt else current[1]

    sections: list[Section] = []
    for i, row in enumerate(deep_schema_rows):
        s = starts[i]
        e = len(blocks) if i == len(starts) - 1 else starts[i + 1]
        if e < s:
            e = s
        html_chunk = "\n".join(blocks[k]["html"] for k in range(s, e)).strip()
        text_chunk = BeautifulSoup(html_chunk, "html.parser").get_text(" ", strip=True)
        title = str(row.get("card_title", row.get("title", ""))).strip()
        if not title:
            continue
        try:
            level = int(row.get("level", 1))
        except (TypeError, ValueError):
            level = 1
        sections.append(
            Section(
                title=title,
                anchor=str(row.get("anchor", "")).strip() or title,
                level=min(max(level, 1), 6),
                page_idx=None,
                html=html_chunk,
                text=text_chunk,
            )
        )
    return sections


def build_deep_cards_from_schema(
    *,
    html: str,
    deep_schema_rows: list[dict],
    toc_items: list[TocItem],
    build_sections_from_llm_schema_rule_based: Callable[[str, list[dict]], list],
    build_rfp_cards: Callable[[list], list],
) -> list:
    # New strategy for step3:
    # 1) interpret LLM output as card candidates
    # 2) split the document by heading boundaries (h1~h6)
    # 3) map candidates to blocks in order and emit cards directly
    heading_blocks = _split_html_by_headings(html)
    sections = _map_schema_to_heading_blocks(deep_schema_rows, heading_blocks)
    if not sections:
        # Fallback to existing path-based splitter
        sections = build_sections_from_llm_schema_rule_based(html, deep_schema_rows)
    paired_sections: list[tuple[Section, dict]] = []
    for s, row in zip(sections, deep_schema_rows):
        for exploded in _split_section_into_top_level_units(s):
            deep_html = _deep_table_list_html(exploded.html)
            deep_text = BeautifulSoup(deep_html, "html.parser").get_text(" ", strip=True)
            exploded.html = deep_html
            exploded.text = deep_text or exploded.text
            paired_sections.append((exploded, row))

    cards: list[RfpCard] = []
    for idx, (section, row) in enumerate(paired_sections, start=1):
        row_title = _clean_title(str(row.get("card_title", row.get("title", ""))))
        section_title = _clean_title(section.title)
        tag = _clean_title(str(row.get("tag", "")).strip().lower())
        toc_title = _find_best_toc_title(toc_items, row_title or section_title or section.text)
        group = toc_title or str(row.get("group", "")).strip() or section.anchor or row_title or section_title
        parent_section = str(row.get("section", "")).strip() or group
        card_title = row_title or section_title or parent_section or group
        if tag == "table" and card_title:
            card_title = f"{parent_section} / {card_title}" if parent_section and parent_section != card_title else card_title
        cards.append(
            RfpCard(
                card_id=idx,
                card_no=str(idx),
                requirement=card_title,
                subject=card_title,
                group=group,
                section=parent_section,
                html_excerpt=section.html or section.text,
                page_idx=section.page_idx,
                anchor=section.anchor,
            )
        )
    return cards
