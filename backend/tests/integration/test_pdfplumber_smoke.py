"""실제 하나.pdf로 변환 결과를 sanity check. 파일 없으면 skip."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.domain.enums import DocumentMime
from app.phase1.converters.pdfplumber_converter import PdfplumberConverter
from app.phase1.loaders.base import GenericLoader

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASE_PATH = PROJECT_ROOT / "evaluator/cases/phase1/html_conversion/hana_pdf.json"


@pytest.mark.integration
def test_pdfplumber_conversion_against_evaluator_case(tmp_path: Path) -> None:
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    src = PROJECT_ROOT / case["input_path"]
    if not src.exists():
        pytest.skip(f"원본 파일 없음: {src}")
    pytest.importorskip("pdfplumber")

    async def run() -> tuple[int, int, str]:
        loader = GenericLoader(DocumentMime.PDF)
        doc = await loader.load(src)
        converter = PdfplumberConverter()
        html_doc = await converter.convert(doc, tmp_path)
        return (
            html_doc.table_count,
            html_doc.paragraph_count,
            html_doc.html_path.read_text(encoding="utf-8"),
        )

    table_count, paragraph_count, html_text = asyncio.run(run())

    expected = case["expected"]
    tolerance = expected["table_count_tolerance"]
    assert (
        abs(table_count - expected["table_count"]) <= tolerance
    ), f"table_count={table_count} 기대={expected['table_count']}±{tolerance}"
    assert paragraph_count >= expected["paragraph_count_min"]
    for kw in expected["must_contain"]:
        assert kw in html_text, f"키워드 누락: {kw!r}"
