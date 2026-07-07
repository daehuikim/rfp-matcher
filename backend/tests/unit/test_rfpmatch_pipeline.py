from __future__ import annotations

from app.llm.fake_client import FakeLlmClient
from prototype.rfpmatch import pipeline
from prototype.rfpmatch.toc_llm import _TocEntry, _TocRows


def test_merge_and_postprocess_tables_merges_consecutive_tables() -> None:
    html = (
        "<table><tr><td>구분</td><td>내용</td></tr><tr><td>A</td><td>1</td></tr></table>"
        "<table><tr><td>구분</td><td>내용</td></tr><tr><td>A</td><td>2</td></tr></table>"
    )
    merged = pipeline._merge_and_postprocess_tables(html)
    assert merged.count("<table") == 1


def test_build_final_toc_applies_recovery_pipeline() -> None:
    html = "<html><body><h1>1. 개요</h1><p>본문</p></body></html>"

    def handler(schema, messages):
        if schema is _TocRows:
            return schema(
                rows=[
                    _TocEntry(level=1, title="목차", page=1),
                    _TocEntry(level=1, title="1. 개요", page=1),
                ]
            )
        raise AssertionError(f"unexpected schema {schema}")

    client = FakeLlmClient(structured_handler=handler)
    toc_items = pipeline._build_final_toc(html, "1. 개요\n본문", source_name="doc", client=client)
    # drop_toc_heading_items가 "목차" 항목을 제거하므로 실제 목차 1건만 남는다.
    assert [item.title for item in toc_items] == ["1. 개요"]


def test_build_final_toc_returns_empty_on_llm_failure() -> None:
    def handler(schema, messages):
        raise RuntimeError("llm down")

    client = FakeLlmClient(structured_handler=handler)
    toc_items = pipeline._build_final_toc(
        "<html></html>", "raw text", source_name="doc", client=client
    )
    assert toc_items == []


def test_run_returns_empty_result_when_conversion_produces_no_html(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pipeline, "convert_any", lambda src, work: {})
    result = pipeline.run("missing.pdf", tmp_path)
    assert result == {
        "section_tables": {},
        "debug_rows": [],
        "cards": [],
        "sections": [],
        "toc_items": [],
        "match_debug": [],
    }
