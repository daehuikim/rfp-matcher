from __future__ import annotations

import logging
from pathlib import Path

from app.core.container import Container
from app.domain.enums import PipelineStage
from app.domain.models import HtmlDoc
from app.phase1.converters.registry import select_converter
from app.phase1.loaders.base import select_loader
from app.services.pipeline import Pipeline

logger = logging.getLogger(__name__)


class ConversionService:
    """
    파일 경로 → HTML 변환 + EventBus publish.

    핸들러는 (state, deps) -> next_state 가까운 형태로 짜서
    FakeLlmClient·임시 디렉토리만으로 결정적 단위테스트가 가능하다.
    """

    def __init__(self, container: Container) -> None:
        self._container = container
        self._pipeline = Pipeline(container.event_bus)

    async def run(self, src_path: Path) -> HtmlDoc:
        loader = select_loader(src_path)
        document = await loader.load(src_path)
        await self._pipeline.emit(document.id, PipelineStage.CONVERTING)
        try:
            converter = select_converter(document.mime)
            out_dir = self._container.settings.storage_root / document.id
            html_doc = await converter.convert(document, out_dir)
        except Exception as e:  # noqa: BLE001
            await self._pipeline.emit_failed(document.id, PipelineStage.CONVERTING, str(e))
            raise
        await self._pipeline.emit(
            document.id,
            PipelineStage.CONVERTED,
            payload={
                "html_path": str(html_doc.html_path),
                "table_count": html_doc.table_count,
                "paragraph_count": html_doc.paragraph_count,
            },
        )
        return html_doc
