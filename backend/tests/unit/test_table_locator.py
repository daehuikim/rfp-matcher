from __future__ import annotations

from pathlib import Path

import pytest

from app.llm.fake_client import FakeLlmClient
from app.phase1.extraction.table_locator import TableLocator


def _write_html(tmp_path: Path, html: str) -> Path:
    p = tmp_path / "doc.html"
    p.write_text(html, encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_heuristic_accepts_table_with_korean_header_keywords(tmp_path: Path) -> None:
    html = """
    <html><body>
      <table>
        <tr><td>요건 구분</td><td>상세 내용</td></tr>
        <tr><td>데이터 수집</td><td>① 원천 시스템 연계</td></tr>
      </table>
      <table>
        <tr><td>foo</td><td>bar</td></tr>
        <tr><td>1</td><td>2</td></tr>
      </table>
    </body></html>
    """
    path = _write_html(tmp_path, html)
    locator = TableLocator(FakeLlmClient(), verify_with_llm=False)

    refs = await locator.locate("doc-1", path)
    assert [r.table_index for r in refs] == [0]
    assert refs[0].located_via == "heuristic"
    assert "상세 내용" in refs[0].header_columns


@pytest.mark.asyncio
async def test_llm_verifies_ambiguous_table(tmp_path: Path) -> None:
    # 4열 중 1열만 키워드 포함 — keyword_ratio=0.25, floor=0.9 → LLM 검증 트리거
    html = """
    <html><body>
      <table>
        <tr><td>구분</td><td>alpha</td><td>beta</td><td>gamma</td></tr>
        <tr><td>X</td><td>Y</td><td>Z</td><td>W</td></tr>
      </table>
    </body></html>
    """
    path = _write_html(tmp_path, html)

    from app.phase1.extraction.table_locator import _LlmVerdict

    def handler(_schema, _msgs):
        return _LlmVerdict(is_requirements_table=True, confidence=0.82)

    locator = TableLocator(
        FakeLlmClient(structured_handler=handler),
        keyword_ratio_floor=0.9,
    )
    refs = await locator.locate("doc-2", path)
    assert len(refs) == 1
    assert refs[0].located_via == "llm"
    assert refs[0].confidence == pytest.approx(0.82)
