from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import mammoth

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter, count_html_features

logger = logging.getLogger(__name__)


class MammothConverter(HtmlConverter):
    """
    python-mammoth — `.docx` → HTML (순수 Python, LibreOffice 불필요).

    https://github.com/mwilliamson/python-mammoth
    - 표 구조는 `<table>` 로 보존 (셀 테두리 스타일은 무시)
    - **구형 `.doc`(OLE)은 미지원** — DocViaDocxConverter 또는 LibreOffice 사용
    """

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        suffix = document.src_path.suffix.lower()
        if suffix != ".docx":
            raise ValueError(
                f"Mammoth는 .docx만 지원합니다 (입력: {suffix}). "
                "구형 .doc은 WORD_CONVERTER=libreoffice 또는 LibreOffice 설치 후 재시도."
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{document.id}.html"

        html_body, messages = await asyncio.to_thread(
            self._convert_sync, document.src_path
        )
        for msg in messages:
            logger.debug("mammoth [%s]: %s", document.id, msg)

        full_html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'/></head>"
            f"<body>{html_body}</body></html>"
        )
        target.write_text(full_html, encoding="utf-8")
        _, paragraphs = count_html_features(full_html)
        table_count = full_html.count("<table")
        logger.info(
            "Mammoth DOCX→HTML doc=%s tables=%d paragraphs≈%d",
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

    @staticmethod
    def _convert_sync(src_path: Path) -> tuple[str, list[str]]:
        with src_path.open("rb") as f:
            result = mammoth.convert_to_html(f)
        messages = [str(m) for m in result.messages]
        return result.value, messages
