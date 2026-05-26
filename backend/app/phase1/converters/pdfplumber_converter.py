from __future__ import annotations

import asyncio
import html
from pathlib import Path

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter, count_html_features


class PdfplumberConverter(HtmlConverter):
    """
    pdfplumber 기반 PDF→HTML. 표는 `<table>`, 비표 텍스트는 `<p>`로 감싸 단순 구조 보존.

    Docling이 사용 가능한 환경이면 DoclingConverter가 1차 — 이 컨버터는 폴백.
    """

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{document.id}.html"
        rendered = await asyncio.to_thread(_render_pdfplumber, document.src_path)
        out_path.write_text(rendered, encoding="utf-8")
        _, paragraphs = count_html_features(rendered)
        return HtmlDoc(
            doc_id=document.id,
            html_path=out_path,
            table_count=rendered.count("<table"),
            paragraph_count=paragraphs,
        )


def _render_pdfplumber(src_path: Path) -> str:
    import pdfplumber

    parts: list[str] = ["<html><body>"]
    with pdfplumber.open(src_path) as pdf:
        for page in pdf.pages:
            parts.append(f'<section data-page="{page.page_number}">')
            tables = page.extract_tables() or []
            for tbl in tables:
                parts.append(_table_to_html(tbl))
            text = page.extract_text() or ""
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
