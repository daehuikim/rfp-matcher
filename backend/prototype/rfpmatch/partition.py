"""카드/표 분할 — step4/6 핵심. rfpmatch/step456_shared.py 이식.

카드를 표/본문 세그먼트로 쪼개거나(step4), 표 카드를 열 경계값 기준으로 더 잘게
쪼개는(step6 부록) 로직. 표를 2차원 매트릭스로 펼치는 유틸도 여기 있다 — rowbuild.py의
표→행 변환이 이 매트릭스 표현에 의존한다.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .cards import _clone_card
from .models import RfpCard
from .sections import _title_key
from .text_utils import (
    _body_text_blocks,
    _cell_text_compact,
    _cell_text_preserve_breaks,
    _cell_text_without_nested_tables,
    _is_heading_like_text,
    _is_title_like_requirement_text,
    _normalize_requirement_text,
    _plain_text_from_html_excerpt,
)
from .toc import extract_lines_from_tag as _extract_lines_from_tag


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


def _carry_table_intro_blocks(prev_blocks: list[dict]) -> list[dict]:
    """표 직전 단락이 표 도입부(완결되지 않은 문장/캡션)로 보이면 표 세그먼트로 끌어온다."""
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
    if last_tag == "figcaption":
        carried.insert(0, prev_blocks.pop())
        if prev_blocks:
            prev_last = prev_blocks[-1]
            prev_tag = str(prev_last.get("tag") or "")
            prev_text = _normalize_requirement_text(str(prev_last.get("text") or ""))
            if prev_tag in {"p", "li", "div"} and prev_text and not _looks_complete(prev_text):
                carried.insert(0, prev_blocks.pop())
    else:
        carried.insert(0, prev_blocks.pop())
    return carried


def _partition_card_into_table_body_segments(card: RfpCard) -> list[RfpCard]:
    html_excerpt = str(card.html_excerpt or "").strip()
    parent_no = getattr(card, "card_no", None) or str(card.card_id)
    fallback = [
        _clone_card(
            card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no
        )
    ]
    if not html_excerpt:
        return fallback

    blocks = _body_text_blocks(html_excerpt)
    if not blocks:
        return fallback

    segments: list[tuple[str, list[dict]]] = []
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
        return fallback

    stage1_cards: list[RfpCard] = []
    parent_page_idx = card.page_idx
    for idx, (kind, segment_blocks) in enumerate(segments, start=1):
        segment_html = "".join(block["html"] for block in segment_blocks).strip()
        if not segment_html:
            continue
        segment_subject = getattr(card, "subject", None) or card.requirement
        if kind == "body":
            candidate_subject = _normalize_requirement_text(
                str(segment_blocks[0].get("text") or "")
            )
            if candidate_subject and (
                _is_title_like_requirement_text(candidate_subject)
                or re.match(
                    r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s*",
                    candidate_subject,
                    flags=re.IGNORECASE,
                )
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

    return stage1_cards or fallback


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
        body_fragment_level = (
            2 if kind == "body" and len(segment_blocks) > 1 else 1 if kind == "body" else None
        )
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


def _inherits_requirement_id_from_previous_table(
    previous_card: RfpCard | None, current_card: RfpCard
) -> bool:
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


def _is_table_followup_common_note_card(
    previous_card: RfpCard | None, current_card: RfpCard
) -> bool:
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
        for line in _plain_text_from_html_excerpt(
            str(getattr(current_card, "html_excerpt", "") or "")
        ).splitlines()
        if _normalize_requirement_text(line)
    ]
    if not lines or len(lines) > 3:
        return False

    first_line = lines[0]
    if not re.match(r"^[\*※]\s*\S+", first_line):
        return False
    return not any(
        re.match(
            r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.]|(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)[\)\.]|[IVXLCDM]+[\)\.])\s+",
            line,
            flags=re.IGNORECASE,
        )
        for line in lines
    )


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
                text = (
                    _cell_text_preserve_breaks(cell)
                    if preserve_breaks
                    else _cell_text_compact(cell)
                )
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


def _table_rows_to_original_html(table_tag: BeautifulSoup) -> list[str]:
    return [str(tr) for tr in table_tag.find_all("tr") if tr.find_parent("table") is table_tag]


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


def _table_has_span(table_tag: BeautifulSoup) -> bool:
    for cell in table_tag.find_all(["th", "td"]):
        rowspan = str(cell.get("rowspan", "1") or "1")
        colspan = str(cell.get("colspan", "1") or "1")
        if rowspan != "1" or colspan != "1":
            return True
    return False


def _table_has_nested_table(table_tag: BeautifulSoup) -> bool:
    return any(nested is not table_tag for nested in table_tag.find_all("table"))


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


def _build_table_html_from_matrix_rows(
    table_tag: BeautifulSoup, matrix_rows: list[list[str]]
) -> str:
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
    data_rows = [
        _norm_row(row) for row in matrix[1:] if any(str(cell or "").strip() for cell in row)
    ]
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
    card_no = str(getattr(card, "card_no", None) or card.card_id)
    fallback = [
        _clone_card(
            card, subject=getattr(card, "subject", None) or card.requirement, card_no=card_no
        )
    ]
    if not html_excerpt:
        return fallback

    if "-t" in card_no:
        return fallback

    title_candidates = [
        str(getattr(card, "subject", None) or "").strip(),
        str(card.requirement or "").strip(),
    ]
    context_title_candidates = [
        *title_candidates,
        str(getattr(card, "section", None) or "").strip(),
    ]
    force_split_keywords = ("요건", "요구", "요청", "이행")
    force_split = any(
        keyword in title for title in title_candidates for keyword in force_split_keywords if title
    )
    if not force_split:
        split_excluded_keywords = ("서식", "현황", "서류", "유의사항", "일정", "제출", "담당자")
        if any(
            keyword in title
            for title in title_candidates
            for keyword in split_excluded_keywords
            if title
        ):
            return fallback
        if any(
            ("당사" in title and "표준" in title) for title in context_title_candidates if title
        ):
            return fallback
        if any(
            ("당사" in title and "시스템" in title) for title in context_title_candidates if title
        ):
            return fallback

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    if not tables:
        return fallback

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
        matrix = _table_visual_matrix(
            table, preserve_breaks=True, strip_nested_tables=has_nested_table
        )
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
                split_table_html = _normalize_nested_tables_in_html(
                    _build_table_html_from_rows(table, split_row_html)
                )
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

    return split_cards or [
        _clone_card(
            card, subject=getattr(card, "subject", None) or card.requirement, card_no=parent_no
        )
    ]


def _find_text_start_index(
    body_blocks: list[dict], text_value: str, start_from: int = 0
) -> int | None:
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


def _split_parent_excerpt_sequentially(parent_html: str, items: list[dict]) -> list[str]:
    blocks = _body_text_blocks(parent_html)
    if not blocks or not items:
        return []

    positions: list[int] = []
    cursor = 0
    for item in items:
        start_text = str(
            item.get("start_text") or item.get("subject") or item.get("requirement") or ""
        ).strip()
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
        start_text = str(
            item.get("start_text") or item.get("subject") or item.get("requirement") or ""
        ).strip()
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
