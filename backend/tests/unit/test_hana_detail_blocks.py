"""하나 상세 요구사항 — ①②③ 단위 셀·연속 표 병합."""
from __future__ import annotations

from prototype.v3.financial_rfp import extract_financial_from_html


_DETAIL_SNIPPET = """
<h6>(2) 상세 요구사항 내용</h6>
<table>
<tr><th><p>요건 구분</p></th><th><p>상세내용</p></th></tr>
<tr>
<td><p>데이터 수집</p></td>
<td><ul>
<li><p>① 원천 시스템 연계</p><ul>
<li><p>• 연계 방식은 각 시스템의 요구사항을 반영하되, 향후 수집 시스템 확대를 감안하여 다양한 인터페이스를 지원해야 합니다.</p></li>
<li><p>• 다양한 원천 데이터 소스에 대한 연결 및 수집 관리 기능을 제공해야 합니다.</p><p>② 지원 파일 형식</p></li>
<li><p>• Pdf, docx, pptx, xlsx, hwp, html, xml 등 다양한 비정형 포맷을 수집 대상으로 지원해야 합니다.</p></li>
</ul></li>
</ul></td>
</tr>
<tr>
<td><p>저장 구조 및 데이터 계층 관리</p></td>
<td><p>① 저장 구조 설계</p><ul>
<li><p>• 원본 데이터는 전처리 단계에서 개인정보 비식별화 처리하여 Object Storage 에 저장하여야 합니다.</p></li>
</ul></td>
</tr>
</table>
<table>
<tr>
<th rowspan="2"></th><th></th>
<th><ul><li><p>• 당행 환경에 적합한 Object Storage 구성 전략을 제안해야 합니다.</p><ul>
<li><p>② 중간 산출물 관리</p><ul>
<li><p>• 데이터 수집, 데이터 변환 등 각 처리 단계의 중간 데이터를 저장해야 합니다.</p></li>
</ul></li>
</ul></li></ul></th>
<th rowspan="2"></th>
</tr>
<tr>
<td><p>워크플로우 오케스트레이션 엔진</p></td>
<td><ul><li><p>• 워크플로우 오케스트레이션 엔진은 GUI 기반으로 설계할 수 있어야 합니다.</p></li></ul></td>
</tr>
</table>
"""


def test_detail_blocks_group_by_circled_number() -> None:
    reqs = extract_financial_from_html(_DETAIL_SNIPPET, "test", use_llm=False)
    data = [r for r in reqs if r.top == "데이터 수집"]
    assert len(data) == 2
    assert data[0].detail.startswith("①")
    assert "• 연계 방식" in data[0].detail
    assert "• 다양한 원천" in data[0].detail
    assert data[1].detail.startswith("②")


def test_storage_then_workflow_in_page_order() -> None:
    reqs = extract_financial_from_html(_DETAIL_SNIPPET, "test", use_llm=False)
    groups = [r.top for r in reqs]
    storage_idx = next(i for i, g in enumerate(groups) if g == "저장 구조 및 데이터 계층 관리")
    workflow_idx = next(i for i, g in enumerate(groups) if g == "워크플로우 오케스트레이션 엔진")
    assert workflow_idx == storage_idx + 3  # ① + ② 후 워크플로우
    assert "Object Storage 구성 전략" in reqs[storage_idx].detail
    assert reqs[storage_idx + 1].detail.startswith("②")
