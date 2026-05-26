from __future__ import annotations

import logging

from app.core.config import Settings, get_settings
from app.domain.enums import DocumentMime

from .base import HtmlConverter
from .libreoffice_converter import LibreOfficeConverter

logger = logging.getLogger(__name__)


def select_converter(mime: DocumentMime, settings: Settings | None = None) -> HtmlConverter:
    """
    MIME → 컨버터 매핑.

    PDF 컨버터는 lifespan에서 한 번만 만들어 Container에 보관한다.
    """
    s = settings or get_settings()
    if mime == DocumentMime.PDF:
        return build_pdf_converter(s)
    if mime in (DocumentMime.DOC, DocumentMime.DOCX, DocumentMime.HWPX):
        return LibreOfficeConverter()
    raise ValueError(f"지원하지 않는 MIME: {mime}")


def build_pdf_converter(settings: Settings) -> HtmlConverter:
    """
    settings.pdf_converter 에 따라 PDF 컨verter 인스턴스를 1개 만든다.

    | 값         | 속도   | 비고                          |
    |------------|--------|-------------------------------|
    | pymupdf    | ★★★   | 기본. GPU/Java 불필요         |
    | pdf2html   | ★★☆   | Java JRE + node tools 필요    |
    | pdfplumber | ★☆☆   | 대용량 PDF 매우 느림          |
    | docling    | ✗      | MPS/mac 불안정, ML 워밍업 큼  |
    """
    name = settings.pdf_converter
    if name == "pymupdf":
        from .pymupdf_converter import PymupdfConverter

        return PymupdfConverter()
    if name == "pdf2html":
        from .pdf2html_converter import Pdf2HtmlConverter

        return Pdf2HtmlConverter()
    if name == "pdfplumber":
        from .pdfplumber_converter import PdfplumberConverter

        return PdfplumberConverter()
    if name == "docling":
        from .docling_converter import DoclingConverter

        logger.warning(
            "PDF_CONVERTER=docling — MPS/mac에서 실패·지연 가능. pymupdf 권장."
        )
        return DoclingConverter()
    raise ValueError(f"unknown pdf_converter: {name}")
