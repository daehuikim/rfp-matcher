from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.models import TableRef
from app.llm.fake_client import FakeLlmClient
from app.phase1.extraction.row_atomizer import RowAtomizer
from app.phase1.extraction.table_columns import (
    extract_category_and_detail,
    header_has_requirement_keyword,
    header_is_requirement_table,
    header_looks_like_requirements,
    is_valid_category_label,
)


def test_header_looks_like_requirements() -> None:
    assert header_looks_like_requirements(["요건 구분", "상세내용"])
    assert not header_looks_like_requirements(["구분", "주요 역할", "제안 기준"])
    assert not header_looks_like_requirements(["운영구분", "모델", "CPU", "GPU", "MEM"])


def test_header_is_requirement_table() -> None:
    assert header_is_requirement_table(["요건 구분", "상세내용"])
    assert header_is_requirement_table(["구분", "구축 범위"])
    assert not header_is_requirement_table(["구분", "주요 역할", "제안 기준"])
    assert not header_has_requirement_keyword(["운영구분", "모델", "CPU", "GPU"])
    assert not header_is_requirement_table(["운영구분", "모델", "CPU", "GPU"])


def test_is_valid_category_label_rejects_requirement_body() -> None:
    assert is_valid_category_label("데이터 수집")
    assert is_valid_category_label("저장 구조 및 데이터 계층 관리")
    assert not is_valid_category_label("비정형 데이터 플랫폼 구축")
    assert not is_valid_category_label(
        "비정형 데이터 플랫폼 구축 측정 ·관리할 수 있는 기준과 지표를 제시할 수 있어야 합니다."
    )
    assert not is_valid_category_label("• 신규 청킹 • 청킹 기능")
    assert not is_valid_category_label(None)


def test_extract_category_skips_merged_title_cell() -> None:
    from bs4 import BeautifulSoup

    html = """
    <table><tr>
      <td>비정형 데이터 플랫폼 구축
• 당행 환경에 적합한 Object Storage 구성 전략을 제안해야 합니다.</td>
      <td></td><td></td><td></td><td></td>
    </tr><tr>
      <td></td>
      <td>워크플로우
오케스트레이션 엔진</td>
      <td>• Low-code 워크플로우 S/W를 제안해야 합니다.</td>
      <td></td><td></td>
    </tr></table>
    """
    row = BeautifulSoup(html, "lxml").find_all("tr")[1]
    cells = row.find_all(["td", "th"])
    category, detail = extract_category_and_detail(cells, category_col=1, detail_col=2)
    assert category == "워크플로우 오케스트레이션 엔진"
    assert "Low-code" in detail


@pytest.mark.asyncio
async def test_pymupdf_continuation_table_parses_detail_column(tmp_path: Path) -> None:
    """PyMuPDF가 페이지 경계에서 조견표를 쪼갠 경우 — 첫 행이 본문이어도 상세열에서 추출."""
    html = """
    <html><body>
      <table>
        <tr>
          <td>비정형 데이터 플랫폼 구축
① 저장 구조
• Object Storage 전략을 제안해야 합니다.
• S3 호환 S/W를 제안해야 합니다.</td>
          <td></td>
          <td>• 당행 환경에 적합한 Object Storage 구성 전략을 제안해야 합니다.
• MinIO 또는 동등 성능 S/W를 제안해야 합니다.</td>
          <td></td>
          <td>비정형 데이터 플랫폼 구축</td>
        </tr>
        <tr>
          <td></td>
          <td>워크플로우</td>
          <td>• Low-code 워크플로우 S/W를 제안해야 합니다.</td>
          <td></td>
          <td></td>
        </tr>
      </table>
    </body></html>
    """
    path = tmp_path / "doc.html"
    path.write_text(html, encoding="utf-8")
    ref = TableRef(
        doc_id="d",
        table_index=0,
        header_columns=["overflow"],
        confidence=1.0,
        located_via="heuristic",
        category_col_index=1,
        detail_col_index=2,
    )
    atoms, _ = await RowAtomizer(FakeLlmClient(), llm_fallback=False).atomize("d", path, ref)
    texts = [a.text for a in atoms]
    assert len(atoms) >= 3
    assert any("Object Storage" in t for t in texts)
    assert any("Low-code" in t for t in texts)
    cats = {a.category_raw for a in atoms}
    assert not any(len(c or "") > 48 for c in cats)
    assert not any("해야 합니다" in (c or "") for c in cats)
