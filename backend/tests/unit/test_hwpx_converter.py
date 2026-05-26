from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.enums import DocumentMime
from app.domain.models import Document
from app.phase1.converters.hwpx_converter import HwpxConverter

_SAMPLE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "raw"
    / "법제처_생성형 AI 법령검색 시스템 구축_RFI_20260116.hwpx"
)


@pytest.mark.asyncio
@pytest.mark.skipif(not _SAMPLE.is_file(), reason="법제처 샘플 hwpx 없음")
async def test_hwpx_converter_extracts_tables(tmp_path: Path) -> None:
    doc = Document(id="hwpx-test", src_path=_SAMPLE, mime=DocumentMime.HWPX)
    html = await HwpxConverter().convert(doc, tmp_path)
    assert html.table_count >= 1
    assert html.html_path.is_file()
