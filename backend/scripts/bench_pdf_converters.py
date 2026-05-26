"""PDF 컨버터 실측 비교."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import uuid
from pathlib import Path

from app.domain.enums import DocumentMime
from app.domain.models import Document
from app.phase1.converters.pdfplumber_converter import PdfplumberConverter
from app.phase1.converters.pymupdf_converter import PymupdfConverter


async def time_converter(name: str, conv, doc: Document, out_root: Path) -> tuple[str, float, int, int]:
    out = out_root / f"{name}-{uuid.uuid4().hex}"
    t0 = time.monotonic()
    html_doc = await conv.convert(doc, out)
    elapsed = time.monotonic() - t0
    return name, elapsed, html_doc.table_count, html_doc.paragraph_count


async def main(pdf_path: Path) -> int:
    doc = Document(id="bench", src_path=pdf_path, mime=DocumentMime.PDF)
    with tempfile.TemporaryDirectory() as tmp:
        out_root = Path(tmp)
        kb = pdf_path.stat().st_size / 1024
        print(f"\n=== bench on {pdf_path.name} ({kb:.0f} KB) ===\n")

        pm = await time_converter("pymupdf", PymupdfConverter(), doc, out_root)
        print(f"  pymupdf    : {pm[1]:6.2f}s  tables={pm[2]} paragraphs={pm[3]}")

        pp = await time_converter("pdfplumber", PdfplumberConverter(), doc, out_root)
        print(f"  pdfplumber : {pp[1]:6.2f}s  tables={pp[2]} paragraphs={pp[3]}")

        if pp[1] > 0:
            print(f"\n  → pymupdf가 pdfplumber보다 {pp[1] / pm[1]:.1f}배 빠름\n")
    return 0


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "../data/raw/하나.pdf").resolve()
    sys.exit(asyncio.run(main(path)))
