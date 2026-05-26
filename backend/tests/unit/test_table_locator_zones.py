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
async def test_requirement_section_zone_requires_requirement_header(tmp_path: Path) -> None:
    """요구사항 섹션 구역 — 조견표 헤더(구분+상세)가 있는 표만 포함."""
    html = """
    <html><body>
      <section data-page="5"><p>1.3 제안 범위</p>
        <table>
          <tr><td>구분</td><td>구축 범위</td></tr>
          <tr><td>Data</td><td>□ 데이터 파이프라인 구축</td></tr>
        </table>
      </section>
      <section data-page="8"><p>2.2 제안 요구사항 상세</p>
        <table>
          <tr><td>구분</td><td>주요 역할</td><td>제안 기준</td></tr>
          <tr><td>Storage</td><td>저장</td><td>S3 호환</td></tr>
        </table>
        <table>
          <tr><td>구분</td><td>요구사항 상세</td></tr>
          <tr><td>공통</td><td>□ 망분리 환경에서 AI 플랫폼 제안</td></tr>
        </table>
        <table>
          <tr><td></td><td>□ 추가 연속 요구사항 본문</td></tr>
        </table>
      </section>
      <section data-page="20"><p>4. 제출 서류</p>
        <table>
          <tr><td>제출 서류</td><td>수량</td></tr>
          <tr><td>제안서</td><td>1부</td></tr>
        </table>
      </section>
    </body></html>
    """
    path = _write_html(tmp_path, html)
    refs = await TableLocator(FakeLlmClient(), verify_with_llm=False).locate("doc", path)
    indices = [r.table_index for r in refs]
    assert 0 in indices
    assert 1 not in indices
    assert 2 in indices
    assert 3 in indices
    assert 4 not in indices


@pytest.mark.asyncio
async def test_detail_requirement_anchor_skips_overview_tables(tmp_path: Path) -> None:
    """「(2) 상세 요구사항 내용」 앵커가 있으면 1.4.3 개요·S/W 역할 표는 제외."""
    html = """
    <html><body>
      <section data-page="7"><p>1.4.3. 상세 요구사항</p>
        <table>
          <tr><td>구분</td><td>주요 역할</td><td>제안 기준</td></tr>
          <tr><td>Storage</td><td>저장</td><td>S3 호환</td></tr>
        </table>
        <table>
          <tr><td></td><td>운영구분</td><td>CPU</td><td>GPU</td></tr>
          <tr><td>개발</td><td>6530P</td><td>2</td><td>4</td></tr>
        </table>
      </section>
      <section data-page="9">
        <table>
          <tr><td>요건 구분</td><td>상세내용</td></tr>
          <tr><td>데이터 수집</td><td>① 원천 시스템 연계</td></tr>
        </table>
        <p>(2) 상세 요구사항 내용</p>
      </section>
      <section data-page="10">
        <table>
          <tr><td></td><td>□ 연속 요구사항 본문</td></tr>
        </table>
      </section>
    </body></html>
    """
    path = _write_html(tmp_path, html)
    refs = await TableLocator(FakeLlmClient(), verify_with_llm=False).locate("doc", path)
    indices = [r.table_index for r in refs]
    assert 0 not in indices
    assert 1 not in indices
    assert 2 in indices
    assert 3 in indices
