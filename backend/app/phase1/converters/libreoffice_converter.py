from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter, count_html_features
from .libreoffice_paths import resolve_soffice_bin

logger = logging.getLogger(__name__)


class LibreOfficeConverter(HtmlConverter):
    """
    LibreOffice headless 변환 — `.doc`/`.docx`/`.hwp` 등 (soffice 필요).
    `soffice --headless --convert-to html:HTML --outdir <out> <src>`
    """

    def __init__(self, soffice_bin: str | None = None) -> None:
        self._bin = resolve_soffice_bin(soffice_bin)

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        out_dir.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            self._bin,
            "--headless",
            "--convert-to",
            "html",
            "--outdir",
            str(out_dir),
            str(document.src_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"soffice 변환 실패 (rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:400]}"
            )
        # soffice는 원본 stem 그대로 .html 생성
        produced = out_dir / f"{document.src_path.stem}.html"
        target = out_dir / f"{document.id}.html"
        if produced.exists():
            produced.replace(target)
        else:  # pragma: no cover — soffice 출력 경로 비표준 환경
            raise RuntimeError(f"soffice 출력 파일을 찾을 수 없음: {produced}; stdout={stdout!r}")

        html_text = target.read_text(encoding="utf-8", errors="replace")
        _, paragraphs = count_html_features(html_text)
        return HtmlDoc(
            doc_id=document.id,
            html_path=target,
            table_count=html_text.count("<table"),
            paragraph_count=paragraphs,
        )
