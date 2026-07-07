from __future__ import annotations

import re
from copy import copy
from html import escape
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", value.strip()).strip("-").lower()
    return cleaned or "section"


def _text_from_spans(block: dict) -> str:
    texts: list[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            content = span.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
    return " ".join(texts).strip()


def _extract_table_html(block: dict) -> str:
    candidates = [
        block.get("html"),
        block.get("table_html"),
        block.get("content"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and "<table" in candidate.lower():
            return candidate
    text = _text_from_spans(block)
    if text:
        return f"<pre>{escape(text)}</pre>"
    return "<p><em>Table content unavailable</em></p>"


def _normalize_html_output(html: str) -> str:
    if not html.strip():
        return html
    return str(BeautifulSoup(html, "html.parser"))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _table_root(table_html: str) -> BeautifulSoup | None:
    if "<table" not in (table_html or "").lower():
        return None
    soup = BeautifulSoup(table_html, "html.parser")
    return soup.find("table")


def _table_row_signature(row) -> str:
    if row is None:
        return ""
    return _clean_text(row.get_text(" ", strip=True)).lower()


def _is_decorative_table_text(text: str) -> bool:
    compact = _clean_text(text)
    if not compact:
        return True
    if re.search(r"[A-Za-z0-9가-힣]", compact):
        return False
    return bool(re.fullmatch(r"[\s\.\-_=·•│\|\u2500-\u257f\/\\]+", compact))


def _is_decorative_table_row(row: Tag) -> bool:
    if getattr(row, "name", None) != "tr":
        return False
    cells = row.find_all(["th", "td"], recursive=False)
    if not cells:
        return False
    texts = [_clean_text(cell.get_text(" ", strip=True)) for cell in cells]
    return all(_is_decorative_table_text(text) for text in texts)


def _prune_decorative_table_rows(table: Tag) -> bool:
    changed = False
    for tr in list(table.find_all("tr")):
        if tr.find_parent("table") is not table:
            continue
        if _is_decorative_table_row(tr):
            tr.decompose()
            changed = True
    return changed


def _table_visual_col_count(table: Tag) -> int:
    max_cols = 0
    _prune_decorative_table_rows(table)
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        if _is_decorative_table_row(tr):
            continue
        cols = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            if _is_decorative_table_text(cell.get_text(" ", strip=True)):
                continue
            try:
                colspan = max(int(cell.get("colspan", 1) or 1), 1)
            except (TypeError, ValueError):
                colspan = 1
            cols += colspan
        max_cols = max(max_cols, cols)
    return max_cols


def _prepend_empty_column(table: Tag, count: int = 1) -> None:
    if count <= 0:
        return
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        first_cell = tr.find(["th", "td"], recursive=False)
        if first_cell is None:
            continue
        for _ in range(count):
            new_cell_name = "th" if first_cell.name == "th" else "td"
            new_cell = BeautifulSoup(f"<{new_cell_name}></{new_cell_name}>", "html.parser").find(
                new_cell_name
            )
            if new_cell is None:
                continue
            new_cell.string = ""
            tr.insert(0, new_cell)


def _promote_leading_empty_cell_to_rowspan(table: Tag) -> bool:
    """Turn the first empty cell in the first row into a table-spanning lead column."""
    tr_nodes = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    if len(tr_nodes) < 3:
        return False
    if _table_visual_col_count(table) != 2:
        return False

    first_row = tr_nodes[0]
    first_cells = first_row.find_all(["th", "td"], recursive=False)
    if len(first_cells) < 2:
        return False

    lead_cell = first_cells[0]
    if not _is_effectively_empty_cell(lead_cell):
        return False
    if int(lead_cell.get("rowspan", 1) or 1) > 1:
        return False

    lead_cell["rowspan"] = str(len(tr_nodes))
    return True


def _is_effectively_empty_cell(cell: Tag | None) -> bool:
    if cell is None:
        return False
    if getattr(cell, "name", None) not in {"th", "td"}:
        return False
    text = _clean_text(cell.get_text(" ", strip=True))
    if text:
        return False
    return cell.find(["img", "svg", "canvas"], recursive=True) is None


def _merge_table_fragments_preserving_outer_html(previous_html: str, current_html: str) -> str:
    prev_table = _table_root(previous_html)
    curr_table = _table_root(current_html)
    if prev_table is None or curr_table is None:
        return previous_html + current_html
    return _merge_consecutive_table_html(str(prev_table), str(curr_table))


def _append_table_fragments_preserving_outer_html(previous_html: str, current_html: str) -> str:
    prev_table = _table_root(previous_html)
    curr_table = _table_root(current_html)
    if prev_table is None or curr_table is None:
        return previous_html + current_html

    curr_rows = curr_table.find_all("tr")
    if not curr_rows:
        return previous_html

    prev_rows = [row for row in prev_table.find_all("tr") if row.find_parent("table") is prev_table]
    prev_header_only = False
    if prev_rows and len(prev_rows) == 1:
        first_row_cells = _row_cells_without_nested_tables(prev_rows[0])
        first_row_texts = [_clean_text(cell.get_text(" ", strip=True)) for cell in first_row_cells]
        prev_header_only = bool(
            first_row_cells
            and (
                all(getattr(cell, "name", None) == "th" for cell in first_row_cells)
                or all(
                    text and len(re.sub(r"\s+", "", text)) <= 20 and not re.search(r"[.?!]$", text)
                    for text in first_row_texts
                )
            )
        )

    # Do not run split-row continuation when the previous fragment is only a
    # carried header table. In that case the next table's first row is real
    # data and must remain its own row rather than being absorbed into header
    # cells.
    if not prev_header_only:
        _merge_clear_split_row_continuation(prev_table, curr_table)
    curr_rows = curr_table.find_all("tr")
    if not curr_rows:
        return str(prev_table)

    for row in curr_rows:
        row_soup = BeautifulSoup(str(row), "html.parser")
        row_tag = row_soup.find("tr")
        if row_tag is not None:
            prev_table.append(row_tag)
    return str(prev_table)


def _clone_tag(tag: Tag) -> Tag:
    cloned = BeautifulSoup(str(tag), "html.parser")
    for child in cloned.contents:
        if isinstance(child, Tag):
            return child
    return cloned


def _tableish_text_without_tables(node: Tag) -> str:
    """Return text content excluding any nested tables."""
    cloned = BeautifulSoup(str(node), "html.parser")
    for table in cloned.find_all("table"):
        table.decompose()
    return _clean_text(cloned.get_text(" ", strip=True))


def _first_table_in_tag(node: Tag) -> Tag | None:
    if getattr(node, "name", None) == "table":
        return node
    return node.find("table")


def _is_transparent_table_gap(node: Tag) -> bool:
    if not isinstance(node, Tag):
        return False
    if node.find("table") is not None:
        return False
    if node.name in {"hr", "br"}:
        return True
    classes = " ".join(node.get("class", [])).lower()
    if "page-break" in classes:
        return True
    text = _clean_text(node.get_text(" ", strip=True))
    if not text:
        return True
    lowered = text.lower()
    if lowered in {"-", "—", "·", "•"}:
        return True
    if lowered.startswith("page:"):
        return True
    if re.fullmatch(r"(page\s*)?\d+(\s*~\s*\d+)?", lowered):
        return True
    # Treat "table label only" snippets as transparent so that a table split
    # across pages can still be merged into one visual table.
    return lowered.startswith("table") and len(lowered.split()) <= 4


def _is_tableish_node(node: Tag) -> bool:
    if not isinstance(node, Tag):
        return False
    if getattr(node, "name", None) == "table":
        return True
    table = _first_table_in_tag(node)
    if table is None:
        return False
    text = _tableish_text_without_tables(node)
    if not text:
        return True
    lowered = text.lower()
    return (
        lowered.startswith("table")
        or lowered.startswith("page:")
        or lowered in {"-", "—"}
        or re.fullmatch(r"(table\s*\|)?\s*page:\s*\d+(\s*~\s*\d+)?", lowered) is not None
    )


def _table_first_row_leading_blank_count(table: Tag) -> int:
    first_row = table.find("tr")
    if first_row is None:
        return 0
    blanks = 0
    for cell in first_row.find_all(["th", "td"], recursive=False):
        if _is_effectively_empty_cell(cell):
            blanks += 1
            continue
        break
    return blanks


def _should_merge_tables(previous_table: Tag, current_table: Tag) -> bool:
    prev_rows = previous_table.find_all("tr")
    curr_rows = current_table.find_all("tr")
    if not curr_rows:
        return False

    prev_first_sig = _table_row_signature(prev_rows[0]) if prev_rows else ""
    curr_first_sig = _table_row_signature(curr_rows[0])
    if prev_first_sig and curr_first_sig and prev_first_sig == curr_first_sig:
        return True

    return _table_first_row_leading_blank_count(current_table) > 0


def _strip_table_labels_from_section(node: Tag) -> None:
    """Remove table-only label/meta blocks left behind after merging."""
    if getattr(node, "name", None) != "section":
        return
    meta = node.find("div", class_=False)
    if meta is not None:
        text = _clean_text(meta.get_text(" ", strip=True)).lower()
        if text.startswith("table"):
            meta.decompose()
    # Also remove orphan table-label paragraphs or divs anywhere inside the section.
    for child in list(node.children):
        if not isinstance(child, Tag):
            continue
        text = _clean_text(child.get_text(" ", strip=True)).lower()
        if (text.startswith("table") or text.startswith("page:")) and len(text.split()) <= 8:
            child.decompose()


def _merge_empty_cells_vertically(table: Tag) -> bool:
    """Fill empty cells from above without changing the cell layout."""
    changed = False

    while True:
        tr_nodes = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        if not tr_nodes:
            return changed

        # Build a visual grid that maps every occupied position to the owning cell.
        grid: dict[tuple[int, int], Tag] = {}
        cell_pos: dict[int, tuple[int, int]] = {}
        cell_span: dict[int, tuple[int, int]] = {}
        max_col = 0

        for r_idx, tr in enumerate(tr_nodes, start=0):
            c_idx = 0
            while (r_idx, c_idx) in grid:
                c_idx += 1
            for cell in tr.find_all(["th", "td"], recursive=False):
                while (r_idx, c_idx) in grid:
                    c_idx += 1
                try:
                    rowspan = max(int(cell.get("rowspan", 1) or 1), 1)
                except (TypeError, ValueError):
                    rowspan = 1
                try:
                    colspan = max(int(cell.get("colspan", 1) or 1), 1)
                except (TypeError, ValueError):
                    colspan = 1
                cell_pos[id(cell)] = (r_idx, c_idx)
                cell_span[id(cell)] = (rowspan, colspan)
                for dr in range(rowspan):
                    for dc in range(colspan):
                        grid[(r_idx + dr, c_idx + dc)] = cell
                c_idx += colspan
                max_col = max(max_col, c_idx)

        def _cell_text(cell: Tag | None) -> str:
            return _clean_text(cell.get_text(" ", strip=True)) if cell is not None else ""

        def _nearest_non_empty_above(r_idx: int, c_idx: int) -> Tag | None:
            # noqa 근거: grid는 while 루프 매 반복 시작 시 새로 채워지고, 이 클로저는
            # 같은 반복 안에서만 즉시 호출·소비된다 — 다음 반복으로 넘어간 뒤 참조되지 않는다.
            for probe_row in range(r_idx - 1, -1, -1):
                candidate = grid.get((probe_row, c_idx))  # noqa: B023
                if candidate is None or candidate is grid.get((r_idx, c_idx)):  # noqa: B023
                    continue
                if _cell_text(candidate):
                    return candidate
            return None

        pass_changed = False
        # Merge when the closest non-empty cell above can safely absorb the
        # current empty cell. This allows blank runs between repeated rowspans
        # while still avoiding unrelated cells in other columns.
        mergeable_cols = max(max_col - 1, 0)
        for r_idx in range(1, len(tr_nodes)):
            for c_idx in range(mergeable_cols):
                cur = grid.get((r_idx, c_idx))
                if cur is None:
                    continue
                cur_text = _cell_text(cur)
                above = _nearest_non_empty_above(r_idx, c_idx)
                if above is None or above is cur:
                    continue
                above_text = _cell_text(above)
                if not above_text or cur_text:
                    continue
                above_row, above_col = cell_pos.get(id(above), (-1, -1))
                cur_row, cur_col = cell_pos.get(id(cur), (-1, -1))
                if above_row < 0 or cur_row < 0:
                    continue
                try:
                    cur_colspan = max(int(cur.get("colspan", 1) or 1), 1)
                except (TypeError, ValueError):
                    cur_colspan = 1
                try:
                    above_colspan = max(int(above.get("colspan", 1) or 1), 1)
                except (TypeError, ValueError):
                    above_colspan = 1
                if not (above_col <= cur_col < above_col + above_colspan):
                    continue
                if cur_colspan > above_colspan:
                    continue
                try:
                    above_rowspan = max(int(above.get("rowspan", 1) or 1), 1)
                except (TypeError, ValueError):
                    above_rowspan = 1
                try:
                    cur_rowspan = max(int(cur.get("rowspan", 1) or 1), 1)
                except (TypeError, ValueError):
                    cur_rowspan = 1
                new_end_row = max(above_row + above_rowspan, cur_row + cur_rowspan)
                above["rowspan"] = str(max(new_end_row - above_row, 1))
                cur.decompose()
                pass_changed = True
                changed = True
                break
            if pass_changed:
                break

        if not pass_changed:
            break

    return changed


def _merge_empty_cells_downward(table: Tag) -> bool:
    """If a cell is empty but the cell below it has content, move the content
    upward and expand the rowspan downward.
    """
    changed = False

    while True:
        tr_nodes = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        if not tr_nodes:
            return changed

        # Build a visual grid that maps every occupied position to the owning cell.
        grid: dict[tuple[int, int], Tag] = {}
        cell_pos: dict[int, tuple[int, int]] = {}
        cell_span: dict[int, tuple[int, int]] = {}
        max_col = 0

        for r_idx, tr in enumerate(tr_nodes, start=0):
            c_idx = 0
            while (r_idx, c_idx) in grid:
                c_idx += 1
            for cell in tr.find_all(["th", "td"], recursive=False):
                while (r_idx, c_idx) in grid:
                    c_idx += 1
                try:
                    rowspan = max(int(cell.get("rowspan", 1) or 1), 1)
                except (TypeError, ValueError):
                    rowspan = 1
                try:
                    colspan = max(int(cell.get("colspan", 1) or 1), 1)
                except (TypeError, ValueError):
                    colspan = 1
                cell_pos[id(cell)] = (r_idx, c_idx)
                cell_span[id(cell)] = (rowspan, colspan)
                for dr in range(rowspan):
                    for dc in range(colspan):
                        grid[(r_idx + dr, c_idx + dc)] = cell
                c_idx += colspan
                max_col = max(max_col, c_idx)

        def _cell_text(cell: Tag | None) -> str:
            return _clean_text(cell.get_text(" ", strip=True)) if cell is not None else ""

        def _is_effectively_empty(cell: Tag | None) -> bool:
            if cell is None:
                return False
            if _cell_text(cell):
                return False
            return cell.find(["img", "svg", "canvas", "table"], recursive=True) is None

        pass_changed = False
        for r_idx in range(1, len(tr_nodes)):
            for c_idx in range(max_col):
                cur = grid.get((r_idx, c_idx))
                above = grid.get((r_idx - 1, c_idx))
                if cur is None or above is None or above is cur:
                    continue
                if _is_effectively_empty(above) and not _is_effectively_empty(cur):
                    above_row, above_col = cell_pos.get(id(above), (-1, -1))
                    cur_row, cur_col = cell_pos.get(id(cur), (-1, -1))
                    if above_row < 0 or cur_row < 0:
                        continue

                    above_rowspan, above_colspan = cell_span.get(id(above), (1, 1))
                    cur_rowspan, cur_colspan = cell_span.get(id(cur), (1, 1))
                    if above_col != cur_col or above_colspan != cur_colspan:
                        continue

                    above.clear()
                    for child in list(cur.contents):
                        above.append(copy(child))

                    new_end_row = max(above_row + above_rowspan, cur_row + cur_rowspan)
                    above["rowspan"] = str(max(new_end_row - above_row, 1))
                    cur.decompose()
                    pass_changed = True
                    changed = True
                    break
            if pass_changed:
                break
        if not pass_changed:
            break

    return changed


def _copy_empty_cells_from_above(table: Tag) -> bool:
    """Fallback: copy the content from the cell above into an empty cell."""
    changed = False

    tr_nodes = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    if not tr_nodes:
        return False

    grid: dict[tuple[int, int], Tag] = {}
    max_col = 0

    for r_idx, tr in enumerate(tr_nodes, start=0):
        c_idx = 0
        while (r_idx, c_idx) in grid:
            c_idx += 1
        for cell in tr.find_all(["th", "td"], recursive=False):
            while (r_idx, c_idx) in grid:
                c_idx += 1
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
                    grid[(r_idx + dr, c_idx + dc)] = cell
            c_idx += colspan
            max_col = max(max_col, c_idx)

    for r_idx in range(1, len(tr_nodes)):
        for c_idx in range(max_col):
            cur = grid.get((r_idx, c_idx))
            above = None
            for probe_row in range(r_idx - 1, -1, -1):
                candidate = grid.get((probe_row, c_idx))
                if candidate is None or candidate is cur:
                    continue
                if _clean_text(candidate.get_text(" ", strip=True)):
                    above = candidate
                    break
            if cur is None or above is None or above is cur:
                continue
            if getattr(cur, "name", None) not in {"td", "th"}:
                continue
            if _clean_text(cur.get_text(" ", strip=True)):
                continue
            above_text = _clean_text(above.get_text(" ", strip=True))
            if not above_text:
                continue
            cur.clear()
            for child in list(above.contents):
                cur.append(copy(child))
            changed = True

    return changed


def _table_has_span_attrs(table: Tag) -> bool:
    for cell in table.find_all(["td", "th"]):
        try:
            if int(cell.get("rowspan", 1) or 1) > 1:
                return True
        except (TypeError, ValueError):
            pass
        try:
            if int(cell.get("colspan", 1) or 1) > 1:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _row_cells_without_nested_tables(row: Tag) -> list[Tag]:
    if getattr(row, "name", None) != "tr":
        return []
    return list(row.find_all(["td", "th"], recursive=False))


def _cell_has_span_attrs(cell: Tag) -> bool:
    try:
        if int(cell.get("rowspan", 1) or 1) > 1:
            return True
    except (TypeError, ValueError):
        return True
    try:
        if int(cell.get("colspan", 1) or 1) > 1:
            return True
    except (TypeError, ValueError):
        return True
    return False


def _cell_text_ends_like_complete_statement(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    return normalized.endswith(("다.", "함", "함.", "됨", "됨.", ".", ":", ")", "]"))


def _looks_like_continuation_cell_text(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    if re.match(r"^(?:[•▪■◆▶◇·ㆍ\-*]|\(?\d+[\.\)]|[가-하][\.\)])\s*", normalized):
        return True
    return bool(re.match(r"^[가-힣A-Za-z0-9]", normalized))


def _looks_like_short_suffix_fragment(text: str) -> bool:
    normalized = _clean_text(text)
    if not normalized:
        return False
    # Be extremely conservative here: only merge rows when the lower fragment
    # is a known meaningless split suffix such as "니터링" from "모니터링".
    # Normal labels like "기타", "개요", "목적" must never be merged.
    allowed_suffix_fragments = {
        "니터링",
    }
    return normalized in allowed_suffix_fragments


def _block_text_lines(block: dict) -> list[str]:
    raw_lines = block.get("lines")
    if isinstance(raw_lines, list) and raw_lines:
        lines: list[str] = []
        for line in raw_lines:
            if not isinstance(line, dict):
                continue
            parts: list[str] = []
            for span in line.get("spans", []) or []:
                content = span.get("content")
                if isinstance(content, str) and content.strip():
                    parts.append(content.strip())
            line_text = re.sub(r"\s+", " ", " ".join(parts)).strip()
            if line_text:
                lines.append(line_text)
        if lines:
            return lines
    text = _text_from_spans(block)
    if not text:
        return []
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    return [line for line in lines if line]


def _looks_like_table_fragment_text(lines: list[str]) -> bool:
    normalized_lines = [_clean_text(line) for line in lines if _clean_text(line)]
    if len(normalized_lines) < 2:
        return False

    label_like = 0
    content_like = 0
    sentence_like = 0
    for line in normalized_lines:
        compact = re.sub(r"\s+", "", line)
        if re.match(
            r"^(?:[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
            line,
            flags=re.IGNORECASE,
        ):
            if re.match(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+\s+", line):
                bullet_body = re.sub(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+\s+", "", compact)
                if len(bullet_body) > 14 and re.search(
                    r"(해야|하여야|제공|지원|수행|구성|관리|검증|확인|포함|필요|제시|운영)",
                    bullet_body,
                ):
                    content_like += 1
                    continue
            label_like += 1
            continue
        if ":" in line or "：" in line:
            label_like += 1
            continue
        if len(compact) <= 18 and not re.search(r"[.?!]$", compact):
            label_like += 1
            continue
        if len(compact) >= 35 or re.search(r"[.?!]$", compact):
            sentence_like += 1
        else:
            content_like += 1

    if sentence_like > 0 and label_like == 0:
        return False
    return label_like >= 1 and (label_like + content_like) >= 2


def _table_fragment_continuity_score(lines: list[str]) -> float:
    normalized_lines = [_clean_text(line) for line in lines if _clean_text(line)]
    if len(normalized_lines) < 2:
        return 0.0
    label_like = 0
    content_like = 0
    sentence_like = 0
    for line in normalized_lines:
        compact = re.sub(r"\s+", "", line)
        if re.match(
            r"^(?:①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩|\(?\d+(?:\.\d+)*[\)\.\-]|[가나다라마바사아자차카타파하][\.\)]|[IVXLCDM]+[\)\.])\s+",
            line,
            flags=re.IGNORECASE,
        ):
            label_like += 1
            continue
        if ":" in line or "：" in line:
            label_like += 1
            continue
        if re.match(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+\s+", line) and (
            len(compact) <= 14
            or (
                len(compact) <= 24
                and not re.search(
                    r"(해야|하여야|제공|지원|수행|구성|관리|검증|확인|포함|필요|제시|운영)", compact
                )
            )
        ):
            label_like += 1
            continue
        if len(compact) >= 35 or re.search(r"[.?!]$", compact):
            sentence_like += 1
        else:
            content_like += 1
    total = label_like + content_like + sentence_like
    if total == 0:
        return 0.0
    if sentence_like > 0 and label_like == 0:
        return 0.0
    return round((label_like + content_like * 0.5) / max(total, 1), 2)


def _looks_like_right_only_table_fragment(lines: list[str]) -> bool:
    normalized_lines = [_clean_text(line) for line in lines if _clean_text(line)]
    if len(normalized_lines) < 2:
        return False

    bullet_like = 0
    sentence_like = 0
    short_heading_like = 0
    for line in normalized_lines:
        compact = re.sub(r"\s+", "", line)
        if re.match(
            r"^(?:[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+|\(?\d+[\)\.\-]|(?:①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩)|[가나다라마바사아자차카타파하][\.\)]|[IVXLCDM]+[\)\.])\s+",
            line,
            flags=re.IGNORECASE,
        ):
            bullet_like += 1
            continue
        if len(compact) <= 12 and not re.search(r"[.?!]$", compact):
            short_heading_like += 1
            continue
        if (
            len(compact) >= 24
            or re.search(r"[.?!]$", compact)
            or re.search(
                r"(해야|하여야|제공|지원|수행|구성|관리|검증|확인|포함|필요|제시|운영)", compact
            )
        ):
            sentence_like += 1

    if short_heading_like >= len(normalized_lines):
        return False
    return bullet_like >= 1 and (bullet_like + sentence_like) >= 2


def _strip_table_fragment_marker(text: str) -> str:
    normalized = _clean_text(text)
    if not normalized:
        return ""
    normalized = re.sub(
        r"^(?:[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized.strip()


def _line_looks_like_table_label(line: str) -> bool:
    compact = _clean_text(line)
    if not compact:
        return False
    if re.match(
        r"^(?:①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩|\(?\d+(?:\.\d+)*[\)\.\-]|[가나다라마바사아자차카타파하][\.\)]|[IVXLCDM]+[\)\.])\s+",
        compact,
        flags=re.IGNORECASE,
    ):
        return True
    if ":" in compact or "：" in compact:
        return True
    if re.match(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+\s+", compact) and (
        len(re.sub(r"\s+", "", compact)) <= 14
        or (
            len(re.sub(r"\s+", "", compact)) <= 24
            and not re.search(
                r"(해야|하여야|제공|지원|수행|구성|관리|검증|확인|포함|필요|제시|운영)", compact
            )
        )
    ):
        return True
    return len(re.sub(r"\s+", "", compact)) <= 18 and not re.search(r"[.?!]$", compact)


def _line_looks_like_structured_row_label(line: str) -> bool:
    compact = _clean_text(line)
    if not compact:
        return False
    if re.match(
        r"^(?:①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩|\(?\d+(?:\.\d+)*[\)\.\-]|[가나다라마바사아자차카타파하][\.\)]|[IVXLCDM]+[\)\.])\s+",
        compact,
        flags=re.IGNORECASE,
    ):
        return True
    if ":" in compact or "：" in compact:
        return True
    return len(re.sub(r"\s+", "", compact)) <= 18 and not re.search(r"[.?!]$", compact)


def _table_fragment_lines_to_html(lines: list[str]) -> str:
    normalized_lines = [_clean_text(line) for line in lines if _clean_text(line)]
    if len(normalized_lines) < 2:
        return ""

    rows: list[tuple[str, str]] = []
    current_label = ""
    current_parts: list[str] = []

    def flush_row() -> None:
        nonlocal current_label, current_parts
        if not current_label:
            current_parts = []
            return
        content = "<br/>".join(
            f"<p>{escape(part)}</p>" if not part.startswith("<") else part
            for part in current_parts
            if _clean_text(part)
        ).strip()
        if not content:
            content = "<p></p>"
        rows.append((current_label, content))
        current_label = ""
        current_parts = []

    for line in normalized_lines:
        if _line_looks_like_table_label(line):
            if current_label:
                flush_row()
            current_label = _strip_table_fragment_marker(line) or _clean_text(line)
            continue
        if current_label:
            current_parts.append(line)
        elif rows:
            prev_label, prev_content = rows[-1]
            rows[-1] = (prev_label, f"{prev_content}<br/>{escape(line)}")
        else:
            current_label = line

    flush_row()
    if not rows:
        return ""
    table_rows = "".join(
        f"<tr><td>{escape(label)}</td><td>{content}</td></tr>" for label, content in rows if label
    )
    return f"<table><tbody>{table_rows}</tbody></table>" if table_rows else ""


def _promote_text_block_to_table_html(block: dict) -> str:
    lines = _block_text_lines(block)
    if not _looks_like_table_fragment_text(lines):
        return ""
    return _table_fragment_lines_to_html(lines)


def _table_fragment_score_label(block: dict) -> str:
    score = block.get("table_fragment_score")
    scores = block.get("table_fragment_scores")
    parts: list[str] = []
    if isinstance(score, (int, float)):
        parts.append(f"fragment score: {float(score):.2f}")
    if isinstance(scores, list) and scores:
        numeric_scores = [float(v) for v in scores if isinstance(v, (int, float))]
        if numeric_scores:
            parts.append(f"fragment scores: {', '.join(f'{v:.2f}' for v in numeric_scores)}")
    return " | ".join(parts)


def _page_indices_are_contiguous(previous_block: dict, current_block: dict) -> bool:
    prev_page = previous_block.get("page_idx")
    prev_end = previous_block.get("page_idx_end")
    curr_page = current_block.get("page_idx")
    if not isinstance(prev_page, int) or not isinstance(curr_page, int):
        return False
    if prev_end is not None and isinstance(prev_end, int):
        return curr_page in {prev_page, prev_end, prev_end + 1}
    return curr_page in {prev_page, prev_page + 1}


def _append_cell_children(target: Tag, source: Tag) -> None:
    if _clean_text(target.get_text(" ", strip=True)) and _clean_text(
        source.get_text(" ", strip=True)
    ):
        target.append(BeautifulSoup("<br/>", "html.parser").br)
    for child in list(source.contents):
        target.append(copy(child))


def _merge_short_suffix_cell_text(target: Tag, suffix_cell: Tag) -> bool:
    target_text = _clean_text(target.get_text(" ", strip=True))
    suffix_text = _clean_text(suffix_cell.get_text(" ", strip=True))
    if not target_text or not suffix_text:
        return False
    target.clear()
    p = BeautifulSoup("", "html.parser").new_tag("p")
    p.string = f"{target_text}{suffix_text}"
    target.append(p)
    return True


def _analyze_split_row_merge(
    previous_cells: list[Tag], current_cells: list[Tag]
) -> tuple[list[int], bool]:
    prev_texts = [_clean_text(cell.get_text(" ", strip=True)) for cell in previous_cells]
    curr_texts = [_clean_text(cell.get_text(" ", strip=True)) for cell in current_cells]
    curr_non_empty = [idx for idx, text in enumerate(curr_texts) if text]
    if not curr_non_empty:
        return [], False

    short_suffix_continuation = (
        len(current_cells) >= 2
        and bool(prev_texts[0])
        and bool(curr_texts[0])
        and _looks_like_short_suffix_fragment(curr_texts[0])
        and not _cell_text_ends_like_complete_statement(prev_texts[0])
    )

    if curr_non_empty == [0] and not short_suffix_continuation:
        return [], False

    mergeable_cols: list[int] = []
    for idx in curr_non_empty:
        prev_text = prev_texts[idx]
        curr_text = curr_texts[idx]
        if not curr_text or not prev_text:
            continue
        if short_suffix_continuation:
            mergeable_cols.append(idx)
            continue
        if _looks_like_continuation_cell_text(
            curr_text
        ) and not _cell_text_ends_like_complete_statement(prev_text):
            mergeable_cols.append(idx)
            continue
        if idx > 0 and not prev_texts[0] and prev_text:
            mergeable_cols.append(idx)
    return mergeable_cols, short_suffix_continuation


def _merge_clear_split_row_pair(
    previous_row: Tag, current_row: Tag, *, require_short_suffix: bool = False
) -> bool:
    prev_cells = _row_cells_without_nested_tables(previous_row)
    curr_cells = _row_cells_without_nested_tables(current_row)
    if not prev_cells or not curr_cells or len(prev_cells) != len(curr_cells):
        return False
    if any(_cell_has_span_attrs(cell) for cell in [*prev_cells, *curr_cells]):
        return False

    mergeable_cols, short_suffix_continuation = _analyze_split_row_merge(prev_cells, curr_cells)
    if not mergeable_cols:
        return False
    if require_short_suffix and not short_suffix_continuation:
        return False

    curr_texts = [_clean_text(cell.get_text(" ", strip=True)) for cell in curr_cells]
    for idx, curr_cell in enumerate(curr_cells):
        curr_text = curr_texts[idx]
        if not curr_text:
            continue
        prev_cell = prev_cells[idx]
        if idx == 0 and short_suffix_continuation:
            _merge_short_suffix_cell_text(prev_cell, curr_cell)
            continue
        if idx in mergeable_cols or not _clean_text(prev_cell.get_text(" ", strip=True)):
            _append_cell_children(prev_cell, curr_cell)

    current_row.decompose()
    return True


def _merge_clear_split_row_continuation(previous_table: Tag, current_table: Tag) -> bool:
    prev_rows = [
        row for row in previous_table.find_all("tr") if row.find_parent("table") is previous_table
    ]
    curr_rows = [
        row for row in current_table.find_all("tr") if row.find_parent("table") is current_table
    ]
    if not prev_rows or not curr_rows:
        return False

    prev_cells = _row_cells_without_nested_tables(prev_rows[-1])
    curr_cells = _row_cells_without_nested_tables(curr_rows[0])
    if not prev_cells or not curr_cells:
        return False

    prev_first = _clean_text(prev_cells[0].get_text(" ", strip=True)) if prev_cells else ""
    curr_first = _clean_text(curr_cells[0].get_text(" ", strip=True)) if curr_cells else ""

    # Cross-table row continuation is only safe when the next table's first row
    # clearly looks like a tail fragment, not a brand-new left-column label such
    # as "데이터 변환", "청킹", "임베딩 및 벡터 검색".
    if curr_first and not _looks_like_short_suffix_fragment(curr_first):
        return False
    if curr_first and prev_first and _cell_text_ends_like_complete_statement(prev_first):
        return False

    return _merge_clear_split_row_pair(prev_rows[-1], curr_rows[0])


def _normalize_split_rows_within_table(table: Tag) -> bool:
    changed = False
    while True:
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        pass_changed = False
        for row_idx in range(1, len(rows)):
            if _merge_clear_split_row_pair(
                rows[row_idx - 1], rows[row_idx], require_short_suffix=True
            ):
                changed = True
                pass_changed = True
                break
        if not pass_changed:
            break
    return changed


def _merge_consecutive_table_html(previous_html: str, current_html: str) -> str:
    """Merge two table HTML fragments into one table, skipping repeated headers."""
    prev_table = _table_root(previous_html)
    curr_table = _table_root(current_html)
    if prev_table is None or curr_table is None:
        return previous_html + current_html

    _prune_decorative_table_rows(prev_table)
    _prune_decorative_table_rows(curr_table)

    prev_cols = _table_visual_col_count(prev_table)
    curr_cols = _table_visual_col_count(curr_table)
    if prev_cols and curr_cols and prev_cols != curr_cols:
        if prev_cols < curr_cols:
            _prepend_empty_column(prev_table, curr_cols - prev_cols)
        else:
            _prepend_empty_column(curr_table, prev_cols - curr_cols)

    prev_rows = prev_table.find_all("tr")
    curr_rows = curr_table.find_all("tr")
    if not curr_rows:
        return previous_html

    prev_first_sig = _table_row_signature(prev_rows[0]) if prev_rows else ""
    curr_first_sig = _table_row_signature(curr_rows[0])
    skip_first_row = bool(prev_first_sig and curr_first_sig and prev_first_sig == curr_first_sig)

    if not skip_first_row:
        _merge_clear_split_row_continuation(prev_table, curr_table)
        curr_rows = curr_table.find_all("tr")
        if not curr_rows:
            return str(prev_table)

    append_rows = curr_rows[1:] if skip_first_row and len(curr_rows) > 1 else curr_rows
    for row in append_rows:
        row_soup = BeautifulSoup(str(row), "html.parser")
        row_tag = row_soup.find("tr")
        if row_tag is not None:
            prev_table.append(row_tag)
    return str(prev_table)


def _merge_continuation_table_blocks(content_list: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for block in content_list:
        block_type = block.get("type", "text")
        promoted_html = ""
        fragment_score = 0.0
        if block_type in {"text", "list"}:
            fragment_lines = _block_text_lines(block)
            fragment_score = _table_fragment_continuity_score(fragment_lines)
            promoted_html = _promote_text_block_to_table_html(block)
            if promoted_html:
                block = dict(block)
                block["table_fragment_score"] = fragment_score

        if block_type == "table" and merged and merged[-1].get("type") == "table":
            prev = merged[-1]
            prev_html = _extract_table_html(prev)
            curr_html = _extract_table_html(block)
            prev["html"] = _merge_consecutive_table_html(prev_html, curr_html)
            prev_start = prev.get("page_idx")
            curr_page = block.get("page_idx")
            if isinstance(prev_start, int) and isinstance(curr_page, int):
                prev["page_idx_end"] = curr_page
            elif "page_idx_end" not in prev:
                prev["page_idx_end"] = prev_start
            prev["table_continuation_count"] = int(prev.get("table_continuation_count", 1)) + 1
            if not prev.get("text"):
                prev["text"] = _clean_text(
                    BeautifulSoup(prev["html"], "html.parser").get_text(" ", strip=True)
                )
            continue

        if (
            promoted_html
            and merged
            and merged[-1].get("type") == "table"
            and _page_indices_are_contiguous(merged[-1], block)
        ):
            prev = merged[-1]
            prev_html = _extract_table_html(prev)
            merged_html = _append_table_fragments_preserving_outer_html(prev_html, promoted_html)
            prev["html"] = merged_html
            prev_scores = list(prev.get("table_fragment_scores") or [])
            prev_scores.append(fragment_score)
            prev["table_fragment_scores"] = prev_scores
            prev["table_fragment_score"] = max(prev_scores) if prev_scores else fragment_score
            prev_start = prev.get("page_idx")
            curr_page = block.get("page_idx")
            if isinstance(prev_start, int) and isinstance(curr_page, int):
                prev["page_idx_end"] = curr_page
            elif "page_idx_end" not in prev:
                prev["page_idx_end"] = prev_start
            prev["table_continuation_count"] = int(prev.get("table_continuation_count", 1)) + 1
            if not prev.get("text"):
                prev["text"] = _clean_text(
                    BeautifulSoup(prev["html"], "html.parser").get_text(" ", strip=True)
                )
            continue

        merged.append(dict(block))
    return merged


def merge_consecutive_tables_in_html(html: str) -> str:
    """Merge adjacent table sections in the rendered HTML output.

    This is a manual post-processing step for tables that were split across page
    boundaries and rendered as back-to-back <section> blocks.
    """
    if not html.strip():
        return html

    soup = BeautifulSoup(html, "html.parser")

    def _replace_table_safely(target: Tag, merged_table: Tag) -> bool:
        parent = target.parent
        if parent is None:
            return False
        try:
            target.insert_before(merged_table)
            target.decompose()
            return True
        except Exception:
            return False

    def _merge_tables_in_parent(parent: Tag) -> bool:
        changed = False
        children = list(parent.contents)
        idx = 0
        while idx < len(children):
            node = children[idx]
            if not isinstance(node, Tag):
                idx += 1
                continue

            # Recurse into non-table containers first so nested table runs are handled too.
            if (
                not _is_tableish_node(node)
                and node.find("table") is not None
                and _merge_tables_in_parent(node)
            ):
                changed = True
                children = list(parent.contents)
                continue

            if not _is_tableish_node(node):
                idx += 1
                continue

            j = idx + 1
            while j < len(children):
                next_node = children[j]
                if not isinstance(next_node, Tag):
                    if _clean_text(str(next_node)):
                        break
                    j += 1
                    continue
                if _is_transparent_table_gap(next_node):
                    j += 1
                    continue
                if not _is_tableish_node(next_node):
                    break
                prev_table = _first_table_in_tag(node)
                curr_table = _first_table_in_tag(next_node)
                if prev_table is None or curr_table is None:
                    break
                if not _should_merge_tables(prev_table, curr_table):
                    break
                merged_html = _merge_table_fragments_preserving_outer_html(
                    str(prev_table), str(curr_table)
                )
                merged_table = BeautifulSoup(merged_html, "html.parser").find("table")
                if merged_table is None:
                    break
                if not _replace_table_safely(prev_table, merged_table):
                    break
                if getattr(next_node, "parent", None) is not None:
                    next_node.decompose()
                changed = True
                children = list(parent.contents)
                j = idx + 1
                continue
            idx += 1
        return changed

    changed = True
    while changed:
        changed = False
        body = soup.body or soup
        if _merge_tables_in_parent(body):
            changed = True

    for table in soup.find_all("table"):
        _normalize_split_rows_within_table(table)
        _prune_decorative_table_rows(table)
        changed = True
        while changed:
            changed = _merge_empty_cells_vertically(table)
            if not changed and not _table_has_span_attrs(table):
                changed = _copy_empty_cells_from_above(table)

    return str(soup)


def merge_consecutive_tables_in_html_raw(html: str) -> str:
    """Merge consecutive tables in document order without empty-cell postprocessing.

    This raw path is intentionally aggressive: if two table-like blocks are
    consecutive and only separated by transparent gaps, we stitch them together
    even when the visual column counts are not identical.
    """
    if not html.strip():
        return html

    soup = BeautifulSoup(html, "html.parser")

    def _replace_table_safely(target: Tag, merged_table: Tag) -> bool:
        parent = target.parent
        if parent is None:
            return False
        try:
            target.insert_before(merged_table)
            target.decompose()
            return True
        except Exception:
            return False

    def _merge_tables_in_parent(parent: Tag) -> bool:
        changed = False
        children = list(parent.contents)
        idx = 0
        while idx < len(children):
            node = children[idx]
            if not isinstance(node, Tag):
                idx += 1
                continue

            if (
                not _is_tableish_node(node)
                and node.find("table") is not None
                and _merge_tables_in_parent(node)
            ):
                changed = True
                children = list(parent.contents)
                continue

            if not _is_tableish_node(node):
                idx += 1
                continue

            j = idx + 1
            while j < len(children):
                next_node = children[j]
                if not isinstance(next_node, Tag):
                    if _clean_text(str(next_node)):
                        break
                    j += 1
                    continue
                if _is_transparent_table_gap(next_node):
                    j += 1
                    continue
                if not _is_tableish_node(next_node):
                    break
                prev_table = _first_table_in_tag(node)
                curr_table = _first_table_in_tag(next_node)
                if prev_table is None or curr_table is None:
                    break
                merged_html = _append_table_fragments_preserving_outer_html(
                    str(prev_table), str(curr_table)
                )
                merged_table = BeautifulSoup(merged_html, "html.parser").find("table")
                if merged_table is None:
                    break
                if not _replace_table_safely(prev_table, merged_table):
                    break
                if getattr(next_node, "parent", None) is not None:
                    next_node.decompose()
                changed = True
                children = list(parent.contents)
                j = idx + 1
                continue
            idx += 1
        return changed

    changed = True
    while changed:
        changed = False
        body = soup.body or soup
        if _merge_tables_in_parent(body):
            changed = True

    for table in soup.find_all("table"):
        _normalize_split_rows_within_table(table)

    return str(soup)


def restore_table_fragments_in_html_raw(html: str) -> str:
    """Restore only high-confidence trailing fragments that follow a 2-col table."""
    if not html.strip():
        return html

    soup = BeautifulSoup(html, "html.parser")

    def _is_header_only_two_col_table(table: Tag) -> bool:
        if _table_visual_col_count(table) != 2:
            return False
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        if len(rows) != 1:
            return False
        cells = _row_cells_without_nested_tables(rows[0])
        if len(cells) != 2:
            return False
        texts = [_clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        left = re.sub(r"\s+", "", texts[0]).lower()
        right = re.sub(r"\s+", "", texts[1]).lower()
        header_pairs = {
            ("구분", "상세내역"),
            ("구분", "상세요구사항"),
            ("구분", "요구사항"),
            ("항목", "상세내역"),
            ("항목", "상세내용"),
            ("분류", "상세내역"),
        }
        if (left, right) in header_pairs:
            return True
        return all(getattr(cell, "name", None) == "th" for cell in cells)

    def _html_node_text_lines(node: Tag) -> list[str]:
        if getattr(node, "name", None) in {"table", "thead", "tbody", "tfoot", "tr"}:
            return []
        if getattr(node, "name", None) in {"ul", "ol", "li"}:
            lines: list[str] = []

            def _walk_list(tag: Tag) -> None:
                direct_text_parts: list[str] = []
                for child in tag.children:
                    if isinstance(child, NavigableString):
                        text = _clean_text(str(child))
                        if text:
                            direct_text_parts.append(text)
                        continue
                    if not isinstance(child, Tag):
                        continue
                    if child.name == "table":
                        continue
                    if child.name == "p":
                        text = _clean_text(child.get_text(" ", strip=True))
                        if text:
                            lines.append(text)
                        continue
                    if child.name in {"ul", "ol", "li"}:
                        _walk_list(child)
                        continue
                    text = _clean_text(child.get_text(" ", strip=True))
                    if text:
                        lines.append(text)
                if direct_text_parts:
                    direct_text = _clean_text(" ".join(direct_text_parts))
                    if direct_text:
                        lines.insert(0, direct_text)

            _walk_list(node)
            return lines
        cloned = BeautifulSoup(str(node), "html.parser")
        for table in cloned.find_all("table"):
            table.decompose()
        text = cloned.get_text("\n", strip=True)
        if not text:
            return []
        return [_clean_text(line) for line in text.split("\n") if _clean_text(line)]

    def _replace_tag_safely(target: Tag, replacement: Tag) -> bool:
        parent = target.parent
        if parent is None:
            return False
        try:
            target.insert_before(replacement)
            target.decompose()
            return True
        except Exception:
            return False

    def _is_heading_like_fragment_start(text: str) -> bool:
        normalized = _clean_text(text)
        if not normalized:
            return False
        return bool(
            re.match(
                r"^(?:\d+(?:\.\d+)+\.?\s+|[IVXLCDM]+\.\s+|제\s*\d+\s*(?:장|절|항)\s+)",
                normalized,
                flags=re.IGNORECASE,
            )
        )

    def _looks_like_toc_fragment(lines: list[str]) -> bool:
        normalized_lines = [_clean_text(line) for line in lines if _clean_text(line)]
        if not normalized_lines:
            return False
        if any("table of contents" in line.lower() for line in normalized_lines):
            return True
        toc_like = 0
        for line in normalized_lines:
            if re.search(r"\.{5,}\s*\d+\s*$", line):
                toc_like += 1
                continue
            if re.match(r"^\d+(?:\.\d+)+\.?\s+.+\d+\s*$", line):
                toc_like += 1
                continue
            if re.match(r"^\d+\.\s+.+\d+\s*$", line):
                toc_like += 1
                continue
        return toc_like >= 2

    def _table_last_row_cells(table: Tag) -> list[Tag]:
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        if not rows:
            return []
        return _row_cells_without_nested_tables(rows[-1])

    def _last_row_right_cell_is_incomplete(table: Tag) -> bool:
        last_cells = _table_last_row_cells(table)
        if len(last_cells) < 2:
            return False
        right_text = _clean_text(last_cells[-1].get_text(" ", strip=True))
        if not right_text:
            return False
        if _cell_text_ends_like_complete_statement(right_text):
            return False
        return len(right_text) >= 2

    def _should_restore_fragment_as_right_only(prev_table: Tag) -> bool:
        last_row_cells = _table_last_row_cells(prev_table)
        if not last_row_cells:
            return False
        if len(last_row_cells) == 1:
            return True
        first_text = _clean_text(last_row_cells[0].get_text(" ", strip=True))
        second_text = (
            _clean_text(last_row_cells[1].get_text(" ", strip=True))
            if len(last_row_cells) > 1
            else ""
        )
        return (not first_text and bool(second_text)) or _last_row_right_cell_is_incomplete(
            prev_table
        )

    def _trailing_fragment_lines_to_table(
        lines: list[str], *, right_only: bool = False
    ) -> Tag | None:
        normalized_lines = [_clean_text(line) for line in lines if _clean_text(line)]
        if len(normalized_lines) < 2:
            return None
        if right_only:
            content_html = "".join(f"<p>{escape(line)}</p>" for line in normalized_lines)
            if not content_html:
                return None
            table_html = f"<table><tbody><tr><td></td><td>{content_html}</td></tr></tbody></table>"
            return BeautifulSoup(table_html, "html.parser").find("table")
        label = _strip_table_fragment_marker(normalized_lines[0]) or normalized_lines[0]
        content_lines = [_clean_text(line) for line in normalized_lines[1:] if _clean_text(line)]
        if not label or not content_lines:
            return None
        content_html = "".join(f"<p>{escape(line)}</p>" for line in content_lines)
        table_html = (
            f"<table><tbody><tr><td>{escape(label)}</td>"
            f"<td>{content_html}</td></tr></tbody></table>"
        )
        return BeautifulSoup(table_html, "html.parser").find("table")

    def _looks_like_label_only_paragraph(text: str) -> bool:
        normalized = _clean_text(text)
        if not normalized:
            return False
        if _is_heading_like_fragment_start(normalized):
            return False
        if len(normalized) > 40:
            return False
        if len(normalized.split()) > 4:
            return False
        return not any(
            token in normalized
            for token in ("합니다", "하여야", "필요", "수행", "제공", "반영", "관리", "검토")
        )

    def _header_only_following_nodes_to_table(group_nodes: list[Tag]) -> Tag | None:
        if len(group_nodes) < 2:
            return None

        has_list = any(getattr(node, "name", None) in {"ul", "ol", "li"} for node in group_nodes)
        has_label_pair = False
        for idx, node in enumerate(group_nodes[:-1]):
            if getattr(node, "name", None) != "p":
                continue
            current_lines = _html_node_text_lines(node)
            next_lines = _html_node_text_lines(group_nodes[idx + 1])
            if (
                len(current_lines) == 1
                and next_lines
                and _looks_like_label_only_paragraph(current_lines[0])
            ):
                has_label_pair = True
                break
        if not has_list and not has_label_pair:
            return None

        rows: list[tuple[str, list[Tag]]] = []
        idx = 0
        while idx < len(group_nodes):
            node = group_nodes[idx]
            if getattr(node, "name", None) not in {"p", "ul", "ol", "li"}:
                idx += 1
                continue

            node_lines = _html_node_text_lines(node)
            if not node_lines:
                idx += 1
                continue

            next_node = group_nodes[idx + 1] if idx + 1 < len(group_nodes) else None
            next_lines = _html_node_text_lines(next_node) if isinstance(next_node, Tag) else []

            if (
                getattr(node, "name", None) == "p"
                and len(node_lines) == 1
                and _looks_like_label_only_paragraph(node_lines[0])
                and isinstance(next_node, Tag)
                and getattr(next_node, "name", None) in {"p", "ul", "ol", "li"}
                and next_lines
            ):
                rows.append((node_lines[0], [next_node]))
                idx += 2
                continue

            if (
                getattr(node, "name", None) == "p"
                and isinstance(next_node, Tag)
                and getattr(next_node, "name", None) in {"ul", "ol", "li"}
                and next_lines
            ):
                rows.append(("", [node, next_node]))
                idx += 2
                continue

            rows.append(("", [node]))
            idx += 1

        row_html_parts: list[str] = []
        for label, content_nodes in rows:
            content_html = "".join(str(_clone_tag(content_node)) for content_node in content_nodes)
            if not _clean_text(
                BeautifulSoup(content_html, "html.parser").get_text(" ", strip=True)
            ):
                continue
            row_html_parts.append(f"<tr><td>{escape(label)}</td><td>{content_html}</td></tr>")

        if not row_html_parts:
            return None
        return BeautifulSoup(
            f"<table><tbody>{''.join(row_html_parts)}</tbody></table>", "html.parser"
        ).find("table")

    def _previous_real_table(children: list, start_idx: int) -> Tag | None:
        prev_idx = start_idx - 1
        while prev_idx >= 0:
            prev_node = children[prev_idx]
            if not isinstance(prev_node, Tag):
                if _clean_text(str(prev_node)):
                    return None
                prev_idx -= 1
                continue
            if _is_transparent_table_gap(prev_node):
                prev_idx -= 1
                continue
            return _first_table_in_tag(prev_node) if _is_tableish_node(prev_node) else None
        return None

    def _restore_in_parent(parent: Tag) -> bool:
        changed = False
        children = list(parent.contents)
        idx = 0
        while idx < len(children):
            node = children[idx]
            if not isinstance(node, Tag):
                idx += 1
                continue

            if (
                not _is_tableish_node(node)
                and node.find("table") is not None
                and _restore_in_parent(node)
            ):
                changed = True
                children = list(parent.contents)
                continue

            if _is_tableish_node(node):
                idx += 1
                continue

            prev_table = _previous_real_table(children, idx)
            if prev_table is None:
                idx += 1
                continue
            if _table_visual_col_count(prev_table) != 2:
                idx += 1
                continue

            group_nodes: list[Tag] = [node]
            group_lines: list[str] = _html_node_text_lines(node)
            next_idx = idx + 1
            while next_idx < len(children):
                next_node = children[next_idx]
                if not isinstance(next_node, Tag):
                    if _clean_text(str(next_node)):
                        break
                    next_idx += 1
                    continue
                if _is_transparent_table_gap(next_node) or _is_tableish_node(next_node):
                    break
                next_lines = _html_node_text_lines(next_node)
                if not next_lines:
                    break
                if _is_heading_like_fragment_start(next_lines[0]):
                    break
                group_nodes.append(next_node)
                group_lines.extend(next_lines)
                next_idx += 1

            if not group_lines:
                idx += 1
                continue
            if _looks_like_toc_fragment(group_lines):
                idx += 1
                continue
            if _is_heading_like_fragment_start(group_lines[0]):
                idx += 1
                continue

            if _is_header_only_two_col_table(prev_table):
                promoted_header_tail = _header_only_following_nodes_to_table(group_nodes)
                if promoted_header_tail is not None:
                    merged_html = _append_table_fragments_preserving_outer_html(
                        str(prev_table), str(promoted_header_tail)
                    )
                    merged_table = BeautifulSoup(merged_html, "html.parser").find("table")
                    if merged_table is not None and _replace_tag_safely(prev_table, merged_table):
                        for group_node in group_nodes:
                            if getattr(group_node, "parent", None) is not None:
                                group_node.decompose()
                        changed = True
                        children = list(parent.contents)
                        idx = max(idx - 1, 0)
                        continue

            right_only = _should_restore_fragment_as_right_only(prev_table)
            inferred_right_only = _looks_like_right_only_table_fragment(group_lines)
            if not right_only and not _line_looks_like_table_label(group_lines[0]):
                if inferred_right_only:
                    right_only = True
                else:
                    idx += 1
                    continue
            if not right_only and not _looks_like_table_fragment_text(group_lines):
                idx += 1
                continue
            if not right_only and _table_fragment_continuity_score(group_lines) < 0.8:
                idx += 1
                continue

            promoted_table = _trailing_fragment_lines_to_table(
                group_lines,
                right_only=right_only,
            )
            if promoted_table is None:
                idx += 1
                continue

            merged_html = _append_table_fragments_preserving_outer_html(
                str(prev_table), str(promoted_table)
            )
            merged_table = BeautifulSoup(merged_html, "html.parser").find("table")
            if merged_table is None:
                idx += 1
                continue
            if not _replace_tag_safely(prev_table, merged_table):
                idx += 1
                continue

            for group_node in group_nodes:
                if getattr(group_node, "parent", None) is not None:
                    group_node.decompose()
            changed = True
            children = list(parent.contents)
            idx = max(idx - 1, 0)

        return changed

    changed = True
    while changed:
        changed = False
        body = soup.body or soup
        if _restore_in_parent(body):
            changed = True

    for table in soup.find_all("table"):
        _merge_empty_cells_downward(table)

    return str(soup)


def promote_standalone_two_col_fragment_html(html: str, title: str = "") -> str:
    """Promote a standalone body fragment into a simple 2-col table when it
    strongly looks like a page-split tail of a 2-level table.
    """
    if not html.strip():
        return html

    soup = BeautifulSoup(html, "html.parser")
    lines = [
        _clean_text(line)
        for line in soup.get_text("\n", strip=True).split("\n")
        if _clean_text(line)
    ]
    if len(lines) < 2:
        return html

    if title:
        cleaned_title = _clean_text(title).replace(" ", "")
        cleaned_first_line = lines[0].replace(" ", "")
        if cleaned_title in cleaned_first_line or cleaned_first_line in cleaned_title:
            return html

    if not _looks_like_right_only_table_fragment(lines):
        return html

    rows: list[tuple[str, list[str]]] = []
    current_label = ""
    current_parts: list[str] = []

    def flush_row() -> None:
        nonlocal current_label, current_parts
        if not current_parts:
            current_label = ""
            return
        rows.append((current_label, list(current_parts)))
        current_label = ""
        current_parts = []

    for line in lines:
        if _line_looks_like_table_label(line):
            flush_row()
            current_label = _strip_table_fragment_marker(line) or _clean_text(line)
            continue
        current_parts.append(line)

    flush_row()
    if not rows:
        return html

    labeled_rows = sum(1 for label, _ in rows if label)
    if labeled_rows < 1:
        return html

    row_html_parts: list[str] = []
    for label, parts in rows:
        content_html = "".join(f"<p>{escape(part)}</p>" for part in parts if _clean_text(part))
        if not content_html:
            continue
        row_html_parts.append(f"<tr><td>{escape(label)}</td><td>{content_html}</td></tr>")

    if not row_html_parts:
        return html
    return f"<table><tbody>{''.join(row_html_parts)}</tbody></table>"


def merge_empty_cells_upward_in_html(html: str) -> str:
    """Collapse empty cells upward using the visual table grid.

    This is a postprocessing pass for already-merged table HTML. It does not
    remove empty columns; it only merges blank cells into the nearest non-empty
    cell above when the visual grid allows it.
    """
    if not html.strip():
        return html

    soup = BeautifulSoup(html, "html.parser")
    changed = True
    while changed:
        changed = False
        for table in soup.find_all("table"):
            if _merge_empty_cells_vertically(table):
                changed = True
    return str(soup)


def add_leading_spanning_blank_column_in_html(html: str) -> str:
    """Promote the first empty cell in matching tables into a spanning lead column."""
    if not html.strip():
        return html

    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for table in soup.find_all("table"):
        if _promote_leading_empty_cell_to_rowspan(table):
            changed = True
    return str(soup) if changed else html


def build_html_document(content_list: list[dict], markdown_path: Path | None) -> str:
    parts = [
        "<html><head><meta charset='utf-8'>"
        "<style>"
        "body{font-family:Arial,sans-serif;line-height:1.6;margin:2rem;}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0;}"
        "th,td{border:1px solid #ccc;padding:0.4rem;vertical-align:top;}"
        ".meta{color:#666;font-size:0.9rem;}"
        ".section{margin-bottom:1.5rem;}"
        ".page-break{border-top:1px dashed #ddd;margin:2rem 0;}"
        "</style></head><body>"
    ]

    if content_list:
        render_blocks = _merge_continuation_table_blocks(content_list)
        for index, block in enumerate(render_blocks):
            block_type = block.get("type", "text")
            page_idx = block.get("page_idx")
            page_end_idx = block.get("page_idx_end")
            text = _text_from_spans(block)
            text_level = int(block.get("text_level", 0) or 0)
            page_label = page_idx if page_idx is not None else "-"
            if (
                isinstance(page_idx, int)
                and isinstance(page_end_idx, int)
                and page_end_idx != page_idx
            ):
                page_label = f"{page_idx}~{page_end_idx}"

            if block_type in {"text", "list"}:
                promoted_html = _promote_text_block_to_table_html(block)
                if promoted_html:
                    table_root = _table_root(promoted_html)
                    if table_root is not None:
                        changed = True
                        while changed:
                            changed = _merge_empty_cells_vertically(
                                table_root
                            ) or _copy_empty_cells_from_above(table_root)
                        fragment_meta = _table_fragment_score_label(block)
                        meta_text = f"page: {page_label}"
                        if fragment_meta:
                            meta_text = f"{meta_text} | {fragment_meta}"
                        parts.append(
                            f"<section class='section' data-page='{page_idx}'>"
                            f"<div class='meta'>{meta_text}</div>"
                            f"{str(table_root)}"
                            "</section>"
                        )
                        continue

            if block_type in {"text", "title"} and text:
                if text_level > 0:
                    level = min(text_level + 1, 6)
                    anchor = _slugify(f"{index}-{text}")
                    parts.append(
                        f"<section class='section' data-page='{page_idx}' id='{anchor}'>"
                        f"<h{level}>{escape(text)}</h{level}>"
                        f"<div class='meta'>page: {page_label}</div>"
                        "</section>"
                    )
                else:
                    parts.append(f"<p data-page='{page_idx}'>{escape(text)}</p>")
            elif block_type == "list" and text:
                items = [item.strip() for item in text.split("  ") if item.strip()]
                if not items:
                    items = [text]
                parts.append("<ul>")
                for item in items:
                    parts.append(f"<li>{escape(item)}</li>")
                parts.append("</ul>")
            elif block_type == "table":
                table_html = _extract_table_html(block)
                table_root = _table_root(table_html)
                if table_root is not None:
                    changed = True
                    while changed:
                        changed = _merge_empty_cells_vertically(
                            table_root
                        ) or _copy_empty_cells_from_above(table_root)
                table_html = str(table_root)
                fragment_meta = _table_fragment_score_label(block)
                meta_text = f"page: {page_label}"
                if fragment_meta:
                    meta_text = f"{meta_text} | {fragment_meta}"
                parts.append(
                    f"<section class='section' data-page='{page_idx}'>"
                    f"<div class='meta'>{meta_text}</div>"
                    f"{table_html}"
                    "</section>"
                )
            elif block_type in {"image", "chart"}:
                caption = text or f"{block_type} block"
                parts.append(
                    f"<figure data-page='{page_idx}'>"
                    f"<figcaption>{escape(caption)}</figcaption>"
                    "</figure>"
                )
            elif text:
                parts.append(f"<div data-page='{page_idx}'>{escape(text)}</div>")

    elif markdown_path and markdown_path.exists():
        markdown_text = markdown_path.read_text(encoding="utf-8")
        try:
            import markdown as md  # type: ignore
        except ImportError:
            parts.append(f"<pre>{escape(markdown_text)}</pre>")
        else:
            parts.append(md.markdown(markdown_text, extensions=["tables", "fenced_code"]))
    else:
        parts.append("<p>No OpenDataLoader output found.</p>")

    parts.append("</body></html>")
    return _normalize_html_output("".join(parts))
