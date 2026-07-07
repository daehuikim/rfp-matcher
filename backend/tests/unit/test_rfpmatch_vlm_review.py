from __future__ import annotations

import json
from types import SimpleNamespace

from prototype.rfpmatch.vlm_review import (
    candidate_to_preview_rows,
    find_suspicious_table_candidates,
    review_candidate_with_vlm,
    rows_to_html_table,
)


def test_find_suspicious_table_candidates_scores_ragged_and_spanned_tables() -> None:
    html = (
        "<table><tr><td rowspan='2'>a</td><td>b</td></tr><tr><td>c</td></tr></table>"
        "<table><tr><td>x</td></tr></table>"
    )
    candidates = find_suspicious_table_candidates(html, limit=5)
    assert len(candidates) == 2
    assert candidates[0].suspicion_score >= candidates[1].suspicion_score


def test_rows_to_html_table_pads_ragged_rows() -> None:
    # 데이터가 헤더(2열)보다 넓은 3열짜리 행을 포함 -> 모든 행/헤더가 3열로 패딩됨.
    html = rows_to_html_table(["구분", "내용"], [["A"], ["B", "1", "2"]])
    assert html.count("<td>") == 6
    assert html.count("<th>") == 3
    assert "<th>구분</th>" in html


def test_candidate_to_preview_rows_limits_to_twelve_rows() -> None:
    html = "<table>" + "".join(f"<tr><td>{i}</td></tr>" for i in range(20)) + "</table>"
    candidates = find_suspicious_table_candidates(html, limit=1)
    preview = candidate_to_preview_rows(candidates[0])
    assert len(preview) == 12


class _FakeResponses:
    def __init__(self, output_text: str) -> None:
        self._output_text = output_text
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self._output_text)


class _FakeOpenAIClient:
    def __init__(self, output_text: str) -> None:
        self.responses = _FakeResponses(output_text)


def test_review_candidate_with_vlm_parses_response_and_sends_system_prompt(tmp_path) -> None:
    candidates = find_suspicious_table_candidates(
        "<table><tr><td>a</td><td>b</td></tr></table>", limit=1
    )
    candidate = candidates[0]
    candidate.page_image_data_uri = "data:image/png;base64,AAA="

    payload = json.dumps(
        {
            "table_title": "표1",
            "confidence": "high",
            "columns": ["구분", "내용"],
            "rows": [["A", "1"]],
            "notes": "",
        }
    )
    fake_client = _FakeOpenAIClient(payload)
    missing_pdf = tmp_path / "missing.pdf"

    reviewed = review_candidate_with_vlm(
        candidate, model_name="gpt-4o", pdf_path=missing_pdf, client=fake_client
    )

    assert reviewed.vlm_result["table_title"] == "표1"
    assert reviewed.vlm_result["confidence"] == "high"
    sent_input = fake_client.responses.calls[0]["input"]
    assert sent_input[0]["role"] == "system"
