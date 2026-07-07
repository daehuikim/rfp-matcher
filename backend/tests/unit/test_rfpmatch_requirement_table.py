from __future__ import annotations

from app.llm.fake_client import FakeLlmClient
from prototype.rfpmatch import requirement_table as rt
from prototype.rfpmatch.models import RfpCard


def test_is_trivial_single_requirement_section_detects_all_same_text() -> None:
    rows = [{"항목명": "x", "요구사항": "x", "상세요건": "x"}]
    assert rt._is_trivial_single_requirement_section(rows) is True


def test_is_trivial_single_requirement_section_rejects_distinct_text() -> None:
    rows = [{"항목명": "a", "요구사항": "b", "상세요건": "c"}]
    assert rt._is_trivial_single_requirement_section(rows) is False


def test_normalize_section_requirement_tables_fills_canonical_keys() -> None:
    tables = {"섹션1": [{"항목명": "a", "요구사항": "b", "상세요건": "c", "card_no": "1"}]}
    normalized = rt._normalize_section_requirement_tables(tables)
    row = normalized["섹션1"][0]
    assert row["항목명"] == "a"
    assert row["card_no"] == "1"
    assert "요구사항 ID" in row


def test_should_route_table_card_to_llm_false_for_supported_two_column() -> None:
    card = RfpCard(
        card_id=1,
        requirement="r",
        html_excerpt="<table><tr><td>a</td><td>b</td></tr></table>",
    )
    assert rt._should_route_table_card_to_llm(card) is False


def test_should_route_table_card_to_llm_true_for_wide_table() -> None:
    card = RfpCard(
        card_id=2,
        requirement="r",
        html_excerpt=("<table><tr><td>a</td><td>b</td><td>c</td><td>d</td><td>e</td></tr></table>"),
    )
    assert rt._should_route_table_card_to_llm(card) is True


def test_section_context_fallback_derives_title_and_prefix() -> None:
    cards = [RfpCard(card_id=1, requirement="요건1", subject="주제1", section="1장")]
    context = rt._section_context_fallback("1장", cards)
    assert context["section_title"] == "1장"
    assert context["default_item_name"] == "주제1"
    assert context["id_prefix"]


def test_merge_schedule_continuation_rows_merges_m_day_offset_lines() -> None:
    rows = [
        {
            "item_name": "일정",
            "requirement": "요건",
            "detail_requirement": "- 착수",
            "result_note": "",
        },
        {
            "item_name": "일정",
            "requirement": "요건",
            "detail_requirement": "M-1개월 데모",
            "result_note": "",
        },
    ]
    merged = rt._merge_schedule_continuation_rows(rows)
    assert len(merged) == 1
    assert "M-1개월 데모" in merged[0]["detail_requirement"]


def test_build_section_requirement_tables_rule_based_table_and_body_cards() -> None:
    table_card = RfpCard(
        card_id=1,
        card_no="1",
        requirement="보안 요건",
        subject="보안",
        section="1장",
        part="Part1",
        html_excerpt=(
            "<table><tr><th>구분</th><th>상세내역</th></tr>"
            "<tr><td>망분리</td><td>물리적 망분리 적용</td></tr></table>"
        ),
    )
    body_card = RfpCard(
        card_id=2,
        card_no="2",
        requirement="일정 요건",
        subject="일정",
        section="1장",
        part="Part1",
        html_excerpt="<p>프로젝트 일정은 다음과 같다.</p>",
    )
    client = FakeLlmClient()  # structured_handler 미설정 — LLM 호출 실패 시 규칙기반 폴백 확인
    tables, debug_rows = rt.build_section_requirement_tables(
        [table_card, body_card], client=client, use_llm=True
    )
    assert len(tables) == 1
    rows = next(iter(tables.values()))
    assert {row["항목명"] for row in rows} == {"보안", "일정"}
    assert all(row["요구사항 ID"] for row in rows)
    assert len(debug_rows) == 2


def test_build_section_requirement_tables_uses_llm_for_unsupported_wide_table() -> None:
    wide_table_card = RfpCard(
        card_id=1,
        card_no="1",
        requirement="복잡표 요건",
        subject="복잡표",
        section="2장",
        part="Part2",
        html_excerpt=(
            "<table><tr><th>a</th><th>b</th><th>c</th><th>d</th><th>e</th></tr>"
            "<tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td></tr></table>"
        ),
    )

    def handler(schema, messages):
        if schema is rt._CardRequirementRows:
            return schema(
                rows=[
                    rt._CardRequirementRow(
                        item_name="항목A",
                        requirement="요구A",
                        detail_requirement="상세A",
                        result_note="",
                    )
                ]
            )
        raise AssertionError(f"unexpected schema {schema}")

    client = FakeLlmClient(structured_handler=handler)
    tables, debug_rows = rt.build_section_requirement_tables(
        [wide_table_card], client=client, use_llm=True
    )
    rows = next(iter(tables.values()))
    assert rows[0]["항목명"] == "항목A"
    assert rows[0]["생성 방식"] == "LLM"
    assert debug_rows[0]["llm_called"] is True


def test_build_section_requirement_tables_invokes_progress_callback() -> None:
    card = RfpCard(
        card_id=1,
        card_no="1",
        requirement="일정 요건",
        subject="일정",
        section="1장",
        html_excerpt="<p>프로젝트 일정은 다음과 같다.</p>",
    )
    messages: list[str] = []
    rt.build_section_requirement_tables(
        [card], client=FakeLlmClient(), use_llm=True, on_progress=messages.append
    )
    assert messages
    assert any("카드 분석 중" in message for message in messages)
