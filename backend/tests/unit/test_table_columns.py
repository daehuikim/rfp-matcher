from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.models import TableRef
from app.llm.fake_client import FakeLlmClient
from app.phase1.extraction.row_atomizer import RowAtomizer
from app.phase1.extraction.table_columns import header_looks_like_requirements


def test_header_looks_like_requirements() -> None:
    assert header_looks_like_requirements(["요건 구분", "상세내용"])
    assert not header_looks_like_requirements(["구분", "주요 역할", "제안 기준"])


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
    atoms = await RowAtomizer(FakeLlmClient(), llm_fallback=False).atomize("d", path, ref)
    texts = [a.text for a in atoms]
    assert len(atoms) >= 3
    assert any("Object Storage" in t for t in texts)
    assert any("Low-code" in t for t in texts)
