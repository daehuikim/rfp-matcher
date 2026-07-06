from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict

from bs4 import BeautifulSoup, Tag


def clean_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def table_cell_text(cell: Tag) -> str:
    text = cell.get_text("\n", strip=False)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    compacted = "\n".join(line for line in lines if line)
    return compacted.strip()


def table_to_grid_with_merges(table: Tag) -> tuple[list[list[str]], list[tuple[int, int, int, int]], list[str]]:
    tr_nodes = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    occupied: dict[tuple[int, int], bool] = {}
    cell_map: dict[int, dict[int, str]] = defaultdict(dict)
    merges: list[tuple[int, int, int, int]] = []
    max_col = 0
    for r_idx, tr in enumerate(tr_nodes, start=1):
        c_idx = 1
        while occupied.get((r_idx, c_idx)):
            c_idx += 1
        for cell in tr.find_all(["th", "td"], recursive=False):
            while occupied.get((r_idx, c_idx)):
                c_idx += 1
            text = table_cell_text(cell)
            try:
                colspan = max(int(cell.get("colspan", 1)), 1)
            except (TypeError, ValueError):
                colspan = 1
            try:
                rowspan = max(int(cell.get("rowspan", 1)), 1)
            except (TypeError, ValueError):
                rowspan = 1
            cell_map[r_idx][c_idx] = text
            if rowspan > 1 or colspan > 1:
                merges.append((r_idx, c_idx, r_idx + rowspan - 1, c_idx + colspan - 1))
            for dr in range(rowspan):
                for dc in range(colspan):
                    if dr == 0 and dc == 0:
                        continue
                    occupied[(r_idx + dr, c_idx + dc)] = True
            max_col = max(max_col, c_idx + colspan - 1)
            c_idx += colspan
    grid_rows: list[list[str]] = []
    for r_idx in range(1, len(tr_nodes) + 1):
        row = [""] * max_col
        for c_idx, value in cell_map.get(r_idx, {}).items():
            if c_idx - 1 < len(row):
                row[c_idx - 1] = value
        grid_rows.append(row)
    headers: list[str] = []
    if grid_rows:
        first_tr = tr_nodes[0] if tr_nodes else None
        first_has_th = bool(first_tr and first_tr.find("th"))
        if first_has_th:
            first_row = grid_rows[0]
            headers = [cell if cell else f"text{i}" for i, cell in enumerate(first_row, start=1)]
            grid_rows = grid_rows[1:]
            merges = [(r1 - 1, c1, r2 - 1, c2) for r1, c1, r2, c2 in merges if r1 > 1 and r2 > 1]
    return grid_rows, merges, headers


def cards_html_excerpt_merged_xlsx_bytes(cards: list) -> bytes:
    def col_name(idx: int) -> str:
        name = ""
        while idx:
            idx, rem = divmod(idx - 1, 26)
            name = chr(65 + rem) + name
        return name

    def xml_escape(value: object) -> str:
        text = "" if value is None else str(value)
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows_xml: list[str] = []
    merge_ranges: list[str] = []
    row_cursor = 1

    def write_row(values: list[str]) -> None:
        nonlocal row_cursor
        cells = []
        for c_idx, value in enumerate(values, start=1):
            if value == "":
                continue
            ref = f"{col_name(c_idx)}{row_cursor}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{xml_escape(value)}</t></is></c>')
        rows_xml.append(f'<row r="{row_cursor}">{"".join(cells)}</row>')
        row_cursor += 1

    for card in cards:
        html_excerpt = str(getattr(card, "html_excerpt", "") or "").strip()
        meta = [
            f"card_id: {getattr(card, 'card_id', '')}",
            f"card_no: {getattr(card, 'card_no', '')}",
            f"requirement: {getattr(card, 'requirement', '')}",
            f"subject: {getattr(card, 'subject', '')}",
            f"category: {getattr(card, 'category', '')}",
            f"part: {getattr(card, 'part', None) or ''}",
            f"section: {getattr(card, 'section', '')}",
        ]
        write_row(meta)
        soup = BeautifulSoup(html_excerpt, "html.parser")
        non_table_blocks = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"])
        seen_text: set[str] = set()
        for block in non_table_blocks:
            if block.find_parent("table") is not None:
                continue
            if block.name == "li" and block.find_parent(["ul", "ol"]) is not None:
                continue
            if block.name == "p" and block.find_parent("li") is not None:
                continue
            text = clean_inline_text(block.get_text(" ", strip=True))
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            write_row([text])
        for table in soup.find_all("table"):
            grid_rows, merges, headers = table_to_grid_with_merges(table)
            if headers:
                write_row(headers)
            start_row = row_cursor
            for grid_row in grid_rows:
                write_row(grid_row)
            for r1, c1, r2, c2 in merges:
                merge_ranges.append(f"{col_name(c1)}{start_row + r1 - 1}:{col_name(c2)}{start_row + r2 - 1}")
        row_cursor += 1

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheetData>"
        + "".join(rows_xml)
        + (f'<mergeCells count="{len(merge_ranges)}">' + "".join(f'<mergeCell ref="{ref}"/>' for ref in merge_ranges) + "</mergeCells>" if merge_ranges else "")
        + "</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="HTML Excerpt" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '</styleSheet>'
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
