from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter
from .libreoffice_paths import resolve_soffice_bin
from .mammoth_converter import MammothConverter

logger = logging.getLogger(__name__)


class DocViaDocxConverter(HtmlConverter):
    """
    구형 `.doc` → LibreOffice로 `.docx` 중간 변환 → Mammoth HTML.

    Mammoth 단독으로는 .doc 불가하므로 docx 단계에만 soffice 필요.
    """

    def __init__(self) -> None:
        self._soffice = resolve_soffice_bin()
        self._mammoth = MammothConverter()

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        if document.src_path.suffix.lower() != ".doc":
            raise ValueError(f"DocViaDocxConverter는 .doc 전용: {document.src_path.suffix}")

        with tempfile.TemporaryDirectory(prefix="rfp-docx-") as tmp:
            tmp_path = Path(tmp)
            docx_path = await self._to_docx(document.src_path, tmp_path)
            docx_doc = document.model_copy(
                update={"src_path": docx_path, "id": document.id}
            )
            logger.info("DOC→DOCX 완료: %s → %s", document.src_path.name, docx_path.name)
            return await self._mammoth.convert(docx_doc, out_dir)

    async def _to_docx(self, src: Path, out_dir: Path) -> Path:
        proc = await asyncio.create_subprocess_exec(
            self._soffice,
            "--headless",
            "--convert-to",
            "docx",
            "--outdir",
            str(out_dir),
            str(src),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"DOC→DOCX 변환 실패 (rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:400]}"
            )
        produced = out_dir / f"{src.stem}.docx"
        if not produced.is_file():
            raise RuntimeError(f"DOC→DOCX 출력 없음: {produced}; stdout={stdout!r}")
        return produced
