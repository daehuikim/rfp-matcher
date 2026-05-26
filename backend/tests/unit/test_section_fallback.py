from __future__ import annotations

import pytest

from app.phase1.extraction.fallback.section_locator import SectionLocator


SAMPLE_HTML = """<!DOCTYPE html><html><body>
<p>1. 프로젝트 개요</p>
<p>개요 본문입니다.</p>
<p>3. 프로젝트 범위</p>
<p>가. AI Platform 구축</p>
<p>1) Workflow Builder 기능 제공</p>
<p>⦁ 실행 흐름 설계 기능</p>
<p>2) Tool 연동 기능</p>
<p>나. 데이터 연계</p>
<p>1) ETL 파이프라인 구축</p>
""" + "\n".join(f"<p>1) 추가 요구사항 항목 {i:02d} 상세 설명 텍스트</p>" for i in range(25)) + """
<p>4. 제출 방법</p>
<p>제출 안내</p>
</body></html>
"""


def test_section_locator_finds_project_scope(tmp_path) -> None:
    html = tmp_path / "doc.html"
    html.write_text(SAMPLE_HTML, encoding="utf-8")
    refs = SectionLocator().locate("doc1", html)
    assert len(refs) == 1
    assert "프로젝트 범위" in refs[0].heading


def test_section_atomizer_splits_subsections(tmp_path) -> None:
    from app.phase1.extraction.fallback.section_atomizer import SectionAtomizer

    html = tmp_path / "doc.html"
    html.write_text(SAMPLE_HTML, encoding="utf-8")
    refs = SectionLocator().locate("doc1", html)
    atoms = SectionAtomizer().atomize("doc1", html, refs[0])
    assert len(atoms) >= 2
    categories = {a.category_raw for a in atoms}
    assert any("가." in (c or "") for c in categories)


@pytest.mark.asyncio
async def test_atomization_coordinator_section_fallback(tmp_path) -> None:
    from app.llm.fake_client import FakeLlmClient
    from app.phase1.extraction.atomization import AtomizationCoordinator, AtomizationStrategy

    html = tmp_path / "doc.html"
    html.write_text(SAMPLE_HTML, encoding="utf-8")
    result = await AtomizationCoordinator(FakeLlmClient()).atomize("doc1", html, [])
    assert result.strategy == AtomizationStrategy.SECTION
    assert len(result.atoms) >= 2


@pytest.mark.asyncio
async def test_atomization_with_tables_skips_section_supplement(tmp_path) -> None:
    from app.domain.models import TableRef
    from app.llm.fake_client import FakeLlmClient
    from app.phase1.extraction.atomization import AtomizationCoordinator, AtomizationStrategy

    html = tmp_path / "doc.html"
    table_html = f"""<html><body>
      <table>
        <tr><td>요건 구분</td><td>상세내용</td></tr>
        <tr><td>공통</td><td>① 표 본문만 추출해야 하며 섹션 서문·S/W 역할 표는 포함하지 않습니다.</td></tr>
      </table>
    {SAMPLE_HTML}
    </body></html>"""
    html.write_text(table_html, encoding="utf-8")
    ref = TableRef(
        doc_id="doc1",
        table_index=0,
        header_columns=["요건 구분", "상세내용"],
        confidence=1.0,
        located_via="heuristic",
        category_col_index=0,
        detail_col_index=1,
    )

    result = await AtomizationCoordinator(FakeLlmClient(), tracker=None).atomize("doc1", html, [ref])
    assert result.strategy == AtomizationStrategy.TABLE
    assert all(a.section_index is None for a in result.atoms)
    assert not any("프로젝트 범위" in (a.text or "") for a in result.atoms)
