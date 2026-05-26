"""evaluator/cases/phase1/extraction/*.json — 기관별 RFP smoke (파일 없으면 skip)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.domain.enums import DocumentMime
from app.llm.fake_client import FakeLlmClient
from app.phase1.converters.pymupdf_converter import PymupdfConverter
from app.phase1.extraction.atomization import AtomizationCoordinator
from app.phase1.extraction.fallback.section_locator import SectionLocator
from app.phase1.extraction.table_locator import TableLocator
from app.phase1.loaders.base import GenericLoader

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASE_DIR = PROJECT_ROOT / "evaluator" / "cases" / "phase1" / "extraction"


def _case_paths() -> list[Path]:
    return sorted(CASE_DIR.glob("*.json"))


@pytest.mark.integration
@pytest.mark.parametrize("case_path", _case_paths(), ids=lambda p: p.stem)
def test_extraction_against_evaluator_case(case_path: Path, tmp_path: Path) -> None:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    src = PROJECT_ROOT / case["input_path"]
    if not src.exists():
        pytest.skip(f"원본 파일 없음: {src}")

    async def run() -> tuple[int, int, int, str, set[str]]:
        loader = GenericLoader(DocumentMime.PDF)
        doc = await loader.load(src)
        html_doc = await PymupdfConverter().convert(doc, tmp_path / case_path.stem)
        table_refs = await TableLocator(
            FakeLlmClient(),
            verify_with_llm=case.get("extraction", {}).get("verify_with_llm", False),
        ).locate(doc.id, html_doc.html_path)
        section_refs = SectionLocator().locate(doc.id, html_doc.html_path)
        atom_result = await AtomizationCoordinator(FakeLlmClient()).atomize(
            doc.id, html_doc.html_path, table_refs
        )
        markers = {a.bullet_marker for a in atom_result.atoms if a.bullet_marker}
        return (
            len(table_refs),
            len(section_refs),
            len(atom_result.atoms),
            atom_result.strategy.value,
            markers,
        )

    tables, sections, atom_count, strategy, markers = asyncio.run(run())
    expected = case["expected"]
    extraction = case.get("extraction", {})

    assert tables >= expected.get("tables_located_min", 0)
    assert sections >= expected.get("sections_located_min", 0)
    assert atom_count >= expected.get("atomic_rows_min", 0)

    if expect_strategy := extraction.get("expect_strategy"):
        assert strategy == expect_strategy

    if expected_any := expected.get("bullet_markers_present_any_of"):
        if atom_count > 0:
            assert markers & set(expected_any), f"기대 마커 없음: 실제={markers!r}"
