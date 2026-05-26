from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.models import TableRef
from app.llm.fake_client import FakeLlmClient
from app.phase1.extraction.row_atomizer import ParagraphAtomizer, RowAtomizer


def _write(tmp_path: Path, html: str) -> Path:
    p = tmp_path / "doc.html"
    p.write_text(html, encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_atomize_splits_circled_markers_in_detail_cell(tmp_path: Path) -> None:
    html = """
    <html><body>
      <table>
        <tr><td>요건 구분</td><td>상세 내용</td></tr>
        <tr>
          <td>데이터 수집</td>
          <td>①원천 시스템 연계
              ②지원 파일 형식
              ③수기 업로드</td>
        </tr>
      </table>
    </body></html>
    """
    path = _write(tmp_path, html)
    atomizer = RowAtomizer(FakeLlmClient(), llm_fallback=False)
    refs = TableRef(
        doc_id="d",
        table_index=0,
        header_columns=["요건 구분", "상세 내용"],
        confidence=1.0,
        located_via="heuristic",
    )
    atoms = await atomizer.atomize("d", path, refs)
    markers = [a.bullet_marker for a in atoms]
    texts = [a.text for a in atoms]
    assert markers == ["①", "②", "③"]
    assert texts == ["원천 시스템 연계", "지원 파일 형식", "수기 업로드"]
    # 모두 같은 좌측 분류를 공유
    assert {a.category_raw for a in atoms} == {"데이터 수집"}


@pytest.mark.asyncio
async def test_paragraph_atomizer_groups_under_heading(tmp_path: Path) -> None:
    html = """
    <html><body>
      <h2>요건</h2>
      <p>① 가나
         ② 라마</p>
      <h2>출력</h2>
      <p>출력 단일 항목</p>
    </body></html>
    """
    path = _write(tmp_path, html)
    atomizer = ParagraphAtomizer()
    atoms = await atomizer.atomize("d", path)
    by_cat = {a.category_raw for a in atoms}
    assert by_cat == {"요건", "출력"}
    # 요건 카테고리에서 ①② 분리됐는지
    yogun = [a for a in atoms if a.category_raw == "요건"]
    assert [a.bullet_marker for a in yogun] == ["①", "②"]
