from __future__ import annotations

import logging
from pathlib import Path

from app.domain.models import Document, HtmlDoc

from .base import HtmlConverter, count_html_features

logger = logging.getLogger(__name__)


class DoclingConverter(HtmlConverter):
    """
    Docling 기반 PDF→HTML. 병합셀·중첩표 보존이 강하다.

    중요: `DocumentConverter`는 layout/OCR 모델을 HuggingFace에서 로드하므로
    **인스턴스 생성 비용이 매우 크다**. 따라서 다음을 강제한다:
      1. `DoclingConverter` 자체를 lifespan singleton으로 만든다 (Container.pdf_converter).
      2. 내부 `DocumentConverter` 도 **첫 호출 시 1회만 생성**해 재사용. 동시 호출이 있어도
         lock으로 직렬 초기화해 모델을 두 번 다운/로드하지 않는다.
      3. HF 캐시는 `main.py`에서 `HF_HOME=data/.cache/huggingface`로 고정 — 재시작에도
         이미 받은 모델을 재사용한다.
    """

    def __init__(self) -> None:
        import asyncio

        self._dc: object | None = None  # docling.document_converter.DocumentConverter
        self._init_lock = asyncio.Lock()

    async def _get_dc(self) -> object:
        if self._dc is not None:
            return self._dc
        async with self._init_lock:
            if self._dc is not None:
                return self._dc
            from docling.document_converter import DocumentConverter

            logger.info("Docling DocumentConverter 초기화 (HF 모델 로드/캐시 확인)…")
            self._dc = DocumentConverter()
            logger.info("Docling DocumentConverter 준비 완료 (singleton)")
            return self._dc

    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc:
        dc = await self._get_dc()

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{document.id}.html"

        # Docling은 동기 API — 이벤트 루프를 블락하지 않게 to_thread로 오프로드.
        import asyncio

        result = await asyncio.to_thread(dc.convert, str(document.src_path))  # type: ignore[attr-defined]

        # Docling API surface는 버전마다 다름 — 가장 흔한 두 경로를 시도
        if hasattr(result, "document") and hasattr(result.document, "export_to_html"):
            html_text = result.document.export_to_html()
        elif hasattr(result, "render_as_html"):
            html_text = result.render_as_html()
        else:
            raise RuntimeError(
                "Docling API에서 HTML 추출 경로를 찾지 못함 — docling 버전 확인 필요"
            )

        out_path.write_text(html_text, encoding="utf-8")
        _, paragraphs = count_html_features(html_text)
        return HtmlDoc(
            doc_id=document.id,
            html_path=out_path,
            table_count=html_text.count("<table"),
            paragraph_count=paragraphs,
        )
