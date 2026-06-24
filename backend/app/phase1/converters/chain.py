from __future__ import annotations

import logging
from pathlib import Path

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter
from .html_postprocess import postprocess_html_file

logger = logging.getLogger(__name__)


class PostprocessConverter(HtmlConverter):
    """변환 성공 후 HTML 후처리(스타일·폰트 제거)를 적용하는 래퍼."""

    def __init__(self, inner: HtmlConverter) -> None:
        self._inner = inner

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        result = await self._inner.convert(document, out_dir)
        tables, paragraphs = postprocess_html_file(result.html_path)
        return result.model_copy(update={"table_count": tables, "paragraph_count": paragraphs})


def with_postprocess(converter: HtmlConverter) -> HtmlConverter:
    return PostprocessConverter(converter)


class FallbackConverter(HtmlConverter):
    """여러 컨버터를 순서대로 시도 — 비정형 포맷별 폴백 체인."""

    def __init__(
        self,
        converters: list[HtmlConverter],
        *,
        names: list[str] | None = None,
    ) -> None:
        if not converters:
            raise ValueError("converters 비어 있음")
        self._converters = converters
        self._names = names or [type(c).__name__ for c in converters]

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        errors: list[str] = []
        for name, converter in zip(self._names, self._converters, strict=True):
            try:
                result = await converter.convert(document, out_dir)
                logger.info(
                    "HTML 변환 성공: %s (%s) doc=%s",
                    document.src_path.name,
                    name,
                    document.id,
                )
                tables, paragraphs = postprocess_html_file(result.html_path)
                return result.model_copy(
                    update={"table_count": tables, "paragraph_count": paragraphs}
                )
            except Exception as e:  # noqa: BLE001
                msg = f"{name}: {e}"
                errors.append(msg)
                logger.warning("HTML 변환 시도 실패 — %s", msg)
        raise RuntimeError(
            f"모든 컨버터 실패 ({document.src_path.name}): " + " | ".join(errors)
        )
