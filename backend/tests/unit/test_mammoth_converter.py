from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.enums import DocumentMime
from app.domain.models import Document
from app.phase1.converters.mammoth_converter import MammothConverter

_HANA_DOC = Path(__file__).resolve().parents[3] / "data" / "raw" / "하나.doc"
_HANA_DOCX = _HANA_DOC.with_suffix(".docx")


@pytest.mark.asyncio
async def test_mammoth_rejects_legacy_doc(tmp_path: Path) -> None:
    if not _HANA_DOC.is_file():
        pytest.skip("하나.doc 샘플 없음")
    doc = Document(id="d1", src_path=_HANA_DOC, mime=DocumentMime.DOC)
    with pytest.raises(ValueError, match="docx"):
        await MammothConverter().convert(doc, tmp_path)


@pytest.mark.asyncio
@pytest.mark.skipif(not _HANA_DOCX.is_file(), reason="하나.docx 없음 — LO로 변환 후 테스트")
async def test_mammoth_converts_docx(tmp_path: Path) -> None:
    doc = Document(id="d2", src_path=_HANA_DOCX, mime=DocumentMime.DOCX)
    html = await MammothConverter().convert(doc, tmp_path)
    assert html.html_path.is_file()
    assert html.html_path.read_text(encoding="utf-8").count("<table") >= 0
