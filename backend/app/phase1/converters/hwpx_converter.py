from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter, count_html_features

logger = logging.getLogger(__name__)

_HP_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_NS = {"hp": _HP_NS}


def _cell_text(cell: ET.Element) -> str:
    parts = [t.text or "" for t in cell.findall(f".//{{{_HP_NS}}}t")]
    return "".join(parts).strip()


def _paragraph_text(p: ET.Element) -> str:
    return _cell_text(p)


class HwpxConverter(HtmlConverter):
    """
    한컴 HWPX(OWPML) → HTML.

    ZIP 내부 Contents/section*.xml 을 파싱 — LibreOffice(soffice) 불필요.
    법제처 RFI 등 .hwpx PoC용.
    """

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{document.id}.html"
        html = self._build_html(document.src_path)
        target.write_text(html, encoding="utf-8")
        _, paragraphs = count_html_features(html)
        table_count = html.count("<table")
        logger.info(
            "HWPX→HTML 완료 doc=%s tables=%d paragraphs≈%d",
            document.id,
            table_count,
            paragraphs,
        )
        return HtmlDoc(
            doc_id=document.id,
            html_path=target,
            table_count=table_count,
            paragraph_count=paragraphs,
        )

    def _build_html(self, src_path: Path) -> str:
        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'/></head><body>"
        ]
        with zipfile.ZipFile(src_path) as zf:
            section_files = sorted(
                n for n in zf.namelist() if n.startswith("Contents/section") and n.endswith(".xml")
            )
            if not section_files:
                raise RuntimeError(f"HWPX에 section XML 없음: {src_path.name}")
            for name in section_files:
                root = ET.fromstring(zf.read(name))
                parts.extend(self._render_section(root))
        parts.append("</body></html>")
        return "\n".join(parts)

    def _render_section(self, root: ET.Element) -> list[str]:
        parent_map: dict[ET.Element, ET.Element] = {}
        for el in root.iter():
            for child in el:
                parent_map[child] = el

        def inside_table(el: ET.Element) -> bool:
            cur: ET.Element | None = el
            while cur is not None and cur in parent_map:
                if cur.tag.endswith("}tbl"):
                    return True
                cur = parent_map.get(cur)
            return False

        out: list[str] = []
        seen_tables: set[int] = set()
        for tbl in root.findall(f".//{{{_HP_NS}}}tbl"):
            tid = id(tbl)
            if tid in seen_tables:
                continue
            seen_tables.add(tid)
            rendered = self._render_table(tbl)
            if rendered:
                out.append(rendered)
        for p in root.findall(f".//{{{_HP_NS}}}p"):
            if inside_table(p):
                continue
            text = _paragraph_text(p)
            if text:
                out.append(f"<p>{_escape(text)}</p>")
        return out

    def _render_table(self, tbl: ET.Element) -> str:
        rows_html: list[str] = []
        for tr in tbl.findall(f".//{{{_HP_NS}}}tr"):
            cells: list[str] = []
            for tc in tr.findall(f"{{{_HP_NS}}}tc"):
                text = _cell_text(tc)
                cells.append(f"<td>{_escape(text)}</td>")
            if cells:
                rows_html.append(f"<tr>{''.join(cells)}</tr>")
        if not rows_html:
            return ""
        return f"<table>{''.join(rows_html)}</table>"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
