from __future__ import annotations

from prototype.v2.grid import Grid
from prototype.v2.llm_schema import execute_deferred_list_block
from prototype.v2.schema import section_has_requirement_context


def test_section_context() -> None:
    assert section_has_requirement_context("2. 제안 요구사항 > 2.2 제안 요구사항 상세")
    assert not section_has_requirement_context("1. 제안 요청 개요")


def test_deferred_list_extracts_requirement_section() -> None:
    g = Grid(
        cells=[["- 시스템은 고가용성으로 구축해야 함"], ["- 백업 정책을 제시해야 함"]],
        table_id=-2,
        page=5,
    )
    sec = "2. 제안 요구사항 > 2.2 제안 요구사항 상세"
    rows = execute_deferred_list_block("t", g, sec)
    assert len(rows) == 2
    assert "리스트" in rows[0].source


def test_deferred_list_skips_overview() -> None:
    g = Grid(cells=[["▪ 사업명: 테스트"]], table_id=-1, page=1)
    assert execute_deferred_list_block("t", g, "1. 제안 요청 개요") == []
