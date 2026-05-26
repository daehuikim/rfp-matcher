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
async def test_heuristic_body_accepts_requirement_keyword_header(tmp_path: Path) -> None:
    html = """
    <html><body>
      <table>
        <tr><td>항목</td><td>요구 내용</td></tr>
        <tr><td>검색</td><td>① 법령 본문 검색 기능을 제공해야 한다</td></tr>
        <tr><td>요약</td><td>② AI 요약 결과를 제공해야 한다</td></tr>
      </table>
    </body></html>
    """
    path = _write_html(tmp_path, html)
    locator = TableLocator(FakeLlmClient(), verify_with_llm=False)
    refs = await locator.locate("doc-2", path)
    assert len(refs) == 1
    assert refs[0].located_via == "heuristic_body"
    assert refs[0].confidence >= 0.75


@pytest.mark.asyncio
async def test_llm_does_not_accept_tables_without_requirement_header(tmp_path: Path) -> None:
    """LLM recall이 켜져도 헤더에 요구·상세 키워드 없는 H/W·역할 표는 제외."""
    html = """
    <html><body>
      <section data-page="5"><p>1.4.</p>
        <table>
          <tr><td></td><td>운영구분</td><td>CPU</td><td>GPU</td></tr>
          <tr><td>개발</td><td>6530P</td><td>6</td><td>4</td></tr>
        </table>
        <table>
          <tr><td>구분</td><td>주요 역할</td><td>제안 기준</td></tr>
          <tr><td>Storage</td><td>저장</td><td>S3 호환</td></tr>
        </table>
      </section>
      <section data-page="9">
        <table>
          <tr><td>요건 구분</td><td>상세내용</td></tr>
          <tr><td>데이터 수집</td><td>① 원천 시스템 연계</td></tr>
        </table>
        <p>(2) 상세 요구사항 내용</p>
      </section>
    </body></html>
    """
    path = _write_html(tmp_path, html)

    from app.phase1.extraction.table_locator import _LlmBatchVerdict, _LlmVerdict

    def always_true(schema, _msgs):
        if schema is _LlmVerdict:
            return _LlmVerdict(is_requirements_table=True, confidence=0.95)
        return _LlmBatchVerdict(
            results=[
                {"table_index": 0, "is_requirements_table": True, "confidence": 0.95},
                {"table_index": 1, "is_requirements_table": True, "confidence": 0.95},
                {"table_index": 2, "is_requirements_table": True, "confidence": 0.95},
            ]
        )

    refs = await TableLocator(FakeLlmClient(structured_handler=always_true)).locate("doc-hw", path)
    indices = [r.table_index for r in refs]
    assert 0 not in indices
    assert 1 not in indices
    assert 2 in indices


@pytest.mark.asyncio
async def test_low_keyword_ratio_table_not_sent_to_llm_without_req_header(tmp_path: Path) -> None:
    html = """
    <html><body>
      <table>
        <tr><td>A</td><td>B</td></tr>
        <tr><td>기능</td><td>시스템은 API를 제공해야 한다</td></tr>
      </table>
    </body></html>
    """
    path = _write_html(tmp_path, html)
    calls: list[str] = []

    from app.phase1.extraction.table_locator import _LlmBatchVerdict, _LlmVerdict

    def handler(schema, msgs):
        calls.append(msgs[0].content)
        item = {"table_index": 0, "is_requirements_table": True, "confidence": 0.75}
        if schema is _LlmVerdict:
            return _LlmVerdict(is_requirements_table=True, confidence=0.75)
        return _LlmBatchVerdict(results=[item])

    locator = TableLocator(FakeLlmClient(structured_handler=handler))
    refs = await locator.locate("doc-3", path)
    assert refs == []
    assert calls == []
