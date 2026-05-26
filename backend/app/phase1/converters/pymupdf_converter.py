from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter, count_html_features

logger = logging.getLogger(__name__)


class PymupdfConverter(HtmlConverter):
    """
    PyMuPDF(fitz) 기반 PDF→HTML.

    pdfplumber 대비 대용량 PDF에서 5~20배 빠르고, Docling과 달리 ML/GPU/MPS 불필요.
    표는 find_tables()로 추출해 `<table>` 유지 — TableLocator/RowAtomizer 호환.
    """

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{document.id}.html"
        rendered = await asyncio.to_thread(_convert_sync, document.src_path)
        out_path.write_text(rendered, encoding="utf-8")
        _, paragraphs = count_html_features(rendered)
        return HtmlDoc(
            doc_id=document.id,
            html_path=out_path,
            table_count=rendered.count("<table"),
            paragraph_count=paragraphs,
        )


def _convert_sync(src_path: Path) -> str:
    import fitz

    parts: list[str] = ["<html><body>"]
    with fitz.open(src_path) as doc:
        for page_num, page in enumerate(doc, start=1):
            parts.append(f'<section data-page="{page_num}">')
            try:
                for tab in page.find_tables().tables:
                    data = tab.extract()
                    if data:
                        parts.append(_table_to_html(data))
            except Exception as e:  # noqa: BLE001
                logger.debug("find_tables skip page %d: %s", page_num, e)
            text = page.get_text("text") or ""
            for line in text.splitlines():
                line = line.strip()
                if line:
                    parts.append(f"<p>{html.escape(line)}</p>")
            parts.append("</section>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _table_to_html(rows: list[list[str | None]]) -> str:
    out = ["<table>"]
    for row in rows:
        out.append("<tr>")
        for cell in row:
            text = html.escape((cell or "").strip())
            out.append(f"<td>{text}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)
