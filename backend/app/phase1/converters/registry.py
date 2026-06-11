from __future__ import annotations

import logging
from collections.abc import Callable

from app.core.config import Settings, get_settings
from app.domain.enums import DocumentMime

from .base import HtmlConverter
from .chain import FallbackConverter, with_postprocess
from .doc_via_docx import DocViaDocxConverter
from .hwp_converter import HwpConverter
from .hwpx_converter import HwpxConverter
from .libreoffice_converter import LibreOfficeConverter
from .mammoth_converter import MammothConverter

logger = logging.getLogger(__name__)

ConverterFactory = Callable[[Settings], HtmlConverter]


class ConverterRegistry:
    """
    MIME → HtmlConverter 팩토리 레지스트리.

    새 비정형 포맷 추가 시 `register()` 한 줄로 확장.
    """

    def __init__(self) -> None:
        self._factories: dict[DocumentMime, ConverterFactory] = {}

    def register(self, mime: DocumentMime, factory: ConverterFactory) -> None:
        self._factories[mime] = factory

    def resolve(self, mime: DocumentMime, settings: Settings | None = None) -> HtmlConverter:
        s = settings or get_settings()
        factory = self._factories.get(mime)
        if factory is None:
            raise ValueError(f"지원하지 않는 MIME: {mime}")
        return factory(s)

    def supported_mimes(self) -> list[DocumentMime]:
        return list(self._factories.keys())


def _build_docx_converter(settings: Settings) -> HtmlConverter:
    mode = settings.word_converter
    mammoth_c = MammothConverter()
    lo_c = LibreOfficeConverter()
    if mode == "libreoffice":
        return lo_c
    if mode == "auto":
        return FallbackConverter([mammoth_c, lo_c], names=["mammoth", "libreoffice"])
    return mammoth_c


def _build_doc_converter(settings: Settings) -> HtmlConverter:
    mode = settings.word_converter
    if mode == "mammoth":
        return DocViaDocxConverter()
    if mode == "auto":
        return FallbackConverter(
            [DocViaDocxConverter(), LibreOfficeConverter()],
            names=["doc-via-docx+mammoth", "libreoffice"],
        )
    return LibreOfficeConverter()


def _build_legacy_hwp_converter(settings: Settings) -> HtmlConverter:
    # 구형 .hwp — pyhwp(hwp5html). macOS LibreOffice는 .hwp 로드 불가.
    return HwpConverter()


def _wrap_pdf(converter: HtmlConverter) -> HtmlConverter:
    return with_postprocess(converter)


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

        return _wrap_pdf(PymupdfConverter())
    if name == "pdf2html":
        from .pdf2html_converter import Pdf2HtmlConverter

        return _wrap_pdf(Pdf2HtmlConverter())
    if name == "pdfplumber":
        from .pdfplumber_converter import PdfplumberConverter

        return _wrap_pdf(PdfplumberConverter())
    if name == "docling":
        from .docling_converter import DoclingConverter

        logger.warning(
            "PDF_CONVERTER=docling — MPS/mac에서 실패·지연 가능. pymupdf 권장."
        )
        return _wrap_pdf(DoclingConverter())
    raise ValueError(f"unknown pdf_converter: {name}")


def build_default_registry() -> ConverterRegistry:
    reg = ConverterRegistry()
    reg.register(DocumentMime.PDF, build_pdf_converter)
    reg.register(DocumentMime.HWPX, lambda _s: with_postprocess(HwpxConverter()))
    reg.register(DocumentMime.DOCX, _build_docx_converter)
    reg.register(DocumentMime.DOC, _build_doc_converter)
    reg.register(DocumentMime.HWP, _build_legacy_hwp_converter)
    return reg


_DEFAULT_REGISTRY = build_default_registry()


def select_converter(mime: DocumentMime, settings: Settings | None = None) -> HtmlConverter:
    """MIME → HtmlConverter (기본 레지스트리)."""
    return _DEFAULT_REGISTRY.resolve(mime, settings)
