"""
HTML 표·셀 → 원자 단위(CellUnit) 청킹.

- `<ul>/<ol>` 안 각 `<li>` = 1 unit (신한 2705-2708 패턴)
- rowspan/colspan 셀은 DOM 기준 1회만 방문
- 셀 안 nested `<table>` / `<img>` 탐지·기록
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag

from prototype.v2.grid import _heading_from_context, read_html_bytes
from prototype.v2.text import norm, sig

_BULLET_LINE = re.compile(r"^\s*[-∙•·–—▪○●]\s+")


@dataclass
class CellUnit:
    uid: str
    table_id: int
    row: int
    col: int
    section: str
    text: str
    kind: str  # bullet | paragraph | nested_table | image | cell_text
    meta: dict = field(default_factory=dict)


@dataclass
class ChunkReport:
    units: list[CellUnit]
    nested_tables: int
    images: int
    bullets: int


def _cell_units_from_tag(
    cell: Tag,
    *,
    table_id: int,
    row: int,
    col: int,
    section: str,
    report: ChunkReport,
) -> list[CellUnit]:
    out: list[CellUnit] = []
    base = f"t{table_id}r{row}c{col}"

    for ni, nested in enumerate(cell.find_all("table")):
        if nested.find_parent("table") is not cell and nested not in cell.find_all("table", recursive=False):
            continue
        if nested.parent is not cell and nested not in cell.descendants:
            continue
        # direct nested only
        if nested.parent is not cell:
            parent_tables = nested.find_parents("table")
            if not parent_tables or parent_tables[0] is not cell:
                continue
        report.nested_tables += 1
        preview = norm(nested.get_text(" ", strip=True))[:120]
        out.append(
            CellUnit(
                uid=f"{base}_nt{ni}",
                table_id=table_id,
                row=row,
                col=col,
                section=section,
                text=preview or "(nested table)",
                kind="nested_table",
                meta={"nested_index": ni},
            )
        )

    for ii, img in enumerate(cell.find_all("img")):
        report.images += 1
        alt = norm(img.get("alt") or img.get("title") or "image")
        src = norm(img.get("src") or "")[:80]
        out.append(
            CellUnit(
                uid=f"{base}_img{ii}",
                table_id=table_id,
                row=row,
                col=col,
                section=section,
                text=alt,
                kind="image",
                meta={"src": src},
            )
        )

    # li 단위 (셀 직속 또는 하위 ul/ol)
    li_index = 0
    for ul in cell.find_all(["ul", "ol"]):
        if ul.find_parent("table") is not cell and ul not in cell.descendants:
            continue
        for li in ul.find_all("li", recursive=False):
            txt = norm(li.get_text(" ", strip=True))
            if len(sig(txt)) < 2:
                continue
            report.bullets += 1
            out.append(
                CellUnit(
                    uid=f"{base}_li{li_index}",
                    table_id=table_id,
                    row=row,
                    col=col,
                    section=section,
                    text=txt,
                    kind="bullet",
                    meta={"list_tag": ul.name},
                )
            )
            li_index += 1

    if out:
        return out

    # bullet 없는 셀 — 줄 단위 또는 전체
    raw = norm(cell.get_text("\n", strip=True))
    if len(sig(raw)) < 2:
        return out

    lines = [norm(ln) for ln in raw.split("\n") if len(sig(norm(ln))) >= 2]
    bullet_lines = [ln for ln in lines if _BULLET_LINE.match(ln)]
    if len(bullet_lines) >= 2:
        for bi, ln in enumerate(bullet_lines):
            body = _BULLET_LINE.sub("", ln).strip()
            if len(sig(body)) < 2:
                continue
            report.bullets += 1
            out.append(
                CellUnit(
                    uid=f"{base}_bl{bi}",
                    table_id=table_id,
                    row=row,
                    col=col,
                    section=section,
                    text=body,
                    kind="bullet",
                    meta={"source": "text_line"},
                )
            )
        return out

    if len(lines) > 1 and all(len(sig(ln)) >= 4 for ln in lines):
        for pi, ln in enumerate(lines):
            out.append(
                CellUnit(
                    uid=f"{base}_p{pi}",
                    table_id=table_id,
                    row=row,
                    col=col,
                    section=section,
                    text=ln,
                    kind="paragraph",
                    meta={},
                )
            )
        return out

    out.append(
        CellUnit(
            uid=f"{base}_cell",
            table_id=table_id,
            row=row,
            col=col,
            section=section,
            text=raw,
            kind="cell_text",
            meta={},
        )
    )
    return out


def chunk_html(html: str) -> ChunkReport:
    soup = BeautifulSoup(html, "lxml")
    top_tables = [t for t in soup.find_all("table") if t.find_parent("table") is None]
    units: list[CellUnit] = []
    report = ChunkReport(units=units, nested_tables=0, images=0, bullets=0)

    for ti, table in enumerate(top_tables):
        section = _heading_from_context(soup, table)
        trs = table.find_all("tr")
        for ri, tr in enumerate(trs):
            col = 0
            for cell in tr.find_all(["td", "th"]):
                rs = int(cell.get("rowspan", 1) or 1)
                cs = int(cell.get("colspan", 1) or 1)
                if rs > 1 or cs > 1:
                    meta_span = {"rowspan": rs, "colspan": cs}
                else:
                    meta_span = {}
                cell_units = _cell_units_from_tag(
                    cell, table_id=ti, row=ri, col=col, section=section, report=report
                )
                for u in cell_units:
                    if meta_span:
                        u.meta.update(meta_span)
                units.extend(cell_units)
                col += cs

    # 표 밖 bullet 단락
    body = soup.body or soup
    for pi, p in enumerate(body.find_all("p")):
        if p.find_parent("table"):
            continue
        txt = norm(p.get_text(" ", strip=True))
        if not _BULLET_LINE.match(txt) or len(sig(txt)) < 4:
            continue
        report.bullets += 1
        units.append(
            CellUnit(
                uid=f"body_p{pi}",
                table_id=-1,
                row=pi,
                col=0,
                section="",
                text=_BULLET_LINE.sub("", txt).strip(),
                kind="bullet",
                meta={"source": "body_p"},
            )
        )

    report.units = units
    return report


def chunk_html_file(path) -> ChunkReport:
    from pathlib import Path

    p = Path(path)
    html = p.read_text(encoding="utf-8", errors="replace")
    return chunk_html(html)


def chunk_html_bytes(raw: bytes) -> ChunkReport:
    return chunk_html(read_html_bytes(raw))
