"""실제 하나.pdf로 M1+M2 end-to-end smoke. 원본 파일 없거나 pdfplumber 없으면 skip."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.domain.enums import DocumentMime
from app.llm.fake_client import FakeLlmClient
from app.phase1.converters.pdfplumber_converter import PdfplumberConverter
from app.phase1.extraction.row_atomizer import RowAtomizer
from app.phase1.extraction.table_locator import TableLocator
from app.phase1.loaders.base import GenericLoader

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASE_PATH = PROJECT_ROOT / "evaluator/cases/phase1/extraction/hana_pdf.json"


@pytest.mark.integration
def test_extraction_against_evaluator_case(tmp_path: Path) -> None:
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    src = PROJECT_ROOT / case["input_path"]
    if not src.exists():
        pytest.skip(f"원본 파일 없음: {src}")
    pytest.importorskip("pdfplumber")

    async def run() -> tuple[int, int, set[str]]:
        loader = GenericLoader(DocumentMime.PDF)
        doc = await loader.load(src)
        html_doc = await PdfplumberConverter().convert(doc, tmp_path)
        locator = TableLocator(FakeLlmClient(), verify_with_llm=False)
        refs = await locator.locate(doc.id, html_doc.html_path)
        atomizer = RowAtomizer(FakeLlmClient(), llm_fallback=False)
        atoms: list = []
        for r in refs:
            atoms.extend(await atomizer.atomize(doc.id, html_doc.html_path, r))
        markers = {a.bullet_marker for a in atoms if a.bullet_marker}
        return len(refs), len(atoms), markers

    tables, atom_count, markers = asyncio.run(run())
    expected = case["expected"]
    assert tables >= expected["tables_located_min"]
    assert atom_count >= expected["atomic_rows_min"]
    expected_any = set(expected["bullet_markers_present_any_of"])
    assert markers & expected_any, f"기대 마커 중 하나도 없음: 실제={markers!r}"
