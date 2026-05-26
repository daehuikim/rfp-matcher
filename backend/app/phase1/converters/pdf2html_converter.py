from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter, count_html_features

logger = logging.getLogger(__name__)

_CONVERT_SCRIPT = PROJECT_ROOT / "backend" / "tools" / "pdf2html" / "convert.mjs"
_TOOLS_DIR = PROJECT_ROOT / "backend" / "tools" / "pdf2html"


class Pdf2HtmlConverter(HtmlConverter):
    """
    shebinleo/pdf2html (Node + Apache Tika/PDFBox + Java JRE) 래퍼.

    Java 미설치 시 RuntimeError — pymupdf로 전환하세요.
    """

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        if not shutil.which("java"):
            raise RuntimeError(
                "pdf2html 변환에 Java JRE가 필요합니다. "
                "brew install openjdk 또는 PDF_CONVERTER=pymupdf 사용"
            )
        if not _CONVERT_SCRIPT.exists():
            raise RuntimeError(f"pdf2html 스크립트 없음: {_CONVERT_SCRIPT}")

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{document.id}.html"
        html_text = await asyncio.to_thread(
            _run_node_convert,
            document.src_path,
            _CONVERT_SCRIPT,
            _TOOLS_DIR,
        )
        out_path.write_text(html_text, encoding="utf-8")
        _, paragraphs = count_html_features(html_text)
        return HtmlDoc(
            doc_id=document.id,
            html_path=out_path,
            table_count=html_text.count("<table"),
            paragraph_count=paragraphs,
        )


def _run_node_convert(pdf_path: Path, script: Path, cwd: Path) -> str:
    proc = subprocess.run(
        ["node", str(script), str(pdf_path.resolve())],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:500]
        raise RuntimeError(f"pdf2html 실패 (rc={proc.returncode}): {err}")
    return proc.stdout
