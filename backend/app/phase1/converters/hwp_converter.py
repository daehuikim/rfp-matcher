from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter, count_html_features

logger = logging.getLogger(__name__)


class HwpConverter(HtmlConverter):
    """구형 .hwp → HTML (pyhwp `hwp5html`). macOS LibreOffice는 .hwp 로드 불가."""

    def __init__(self, hwp5html_bin: str | None = None) -> None:
        self._bin = hwp5html_bin or shutil.which("hwp5html") or "hwp5html"

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        work = out_dir / "_hwp5"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            self._bin,
            "--output",
            str(work),
            str(document.src_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"hwp5html 변환 실패 (rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:400]}"
            )

        produced = work / "index.xhtml"
        if not produced.is_file():
            raise RuntimeError(
                f"hwp5html 출력 없음: {produced}; stdout={stdout!r}"
            )

        target = out_dir / f"{document.id}.html"
        shutil.copyfile(produced, target)
        html_text = target.read_text(encoding="utf-8", errors="replace")
        _, paragraphs = count_html_features(html_text)
        logger.info(
            "HWP→HTML 완료 doc=%s tables=%d paragraphs≈%d",
            document.id,
            html_text.count("<table"),
            paragraphs,
        )
        return HtmlDoc(
            doc_id=document.id,
            html_path=target,
            table_count=html_text.count("<table"),
            paragraph_count=paragraphs,
        )
