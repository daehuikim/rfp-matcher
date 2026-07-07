from __future__ import annotations

from app.llm.fake_client import FakeLlmClient
from prototype.rfpmatch.toc_llm import _TocEntry, _TocRows, build_llm_toc_from_raw_document


def _fake_client(rows: list[_TocEntry]) -> FakeLlmClient:
    def handler(schema: type, messages: list) -> _TocRows:
        return schema(rows=rows)

    return FakeLlmClient(structured_handler=handler)


def test_build_llm_toc_from_raw_document_returns_toc_items() -> None:
    client = _fake_client(
        [
            _TocEntry(level=1, title="1. 사업 개요", page=3),
            _TocEntry(level=1, title="2. 사업 범위", page=5),
        ]
    )
    items = build_llm_toc_from_raw_document("본문 텍스트", "source.txt", client=client)
    assert [i.title for i in items] == ["1. 사업 개요", "2. 사업 범위"]
    assert [i.page_idx for i in items] == [3, 5]


def test_build_llm_toc_from_raw_document_returns_empty_for_blank_input() -> None:
    client = _fake_client([_TocEntry(level=1, title="무시됨", page=1)])
    items = build_llm_toc_from_raw_document("   ", "source.txt", client=client)
    assert items == []


def test_build_llm_toc_from_raw_document_retries_on_missing_numbering_sequence() -> None:
    calls: list[list] = []

    def handler(schema: type, messages: list) -> _TocRows:
        calls.append(messages)
        if len(calls) == 1:
            return schema(
                rows=[
                    _TocEntry(level=1, title="1.1 사업개요", page=1),
                    _TocEntry(level=1, title="1.3 추진일정", page=3),
                ]
            )
        return schema(
            rows=[
                _TocEntry(level=1, title="1.1 사업개요", page=1),
                _TocEntry(level=1, title="1.2 사업범위", page=2),
                _TocEntry(level=1, title="1.3 추진일정", page=3),
            ]
        )

    client = FakeLlmClient(structured_handler=handler)
    items = build_llm_toc_from_raw_document("본문 텍스트", "source.txt", client=client)
    assert len(calls) == 2
    assert {i.title for i in items} == {"1.1 사업개요", "1.2 사업범위", "1.3 추진일정"}
