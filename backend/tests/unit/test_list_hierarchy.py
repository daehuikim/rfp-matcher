from __future__ import annotations

from prototype.v2.extract import Req
from prototype.v2.ids import assign_ids
from prototype.v2.list_hierarchy import refine_list_hierarchy


def test_hangul_top_becomes_item_name() -> None:
    reqs = [
        Req(doc="t", table_id=-1, page=6, tab="구축방향", source="p.6 · 리스트",
            detail="가. 지식베이스 구축(RAG)"),
        Req(doc="t", table_id=-1, page=6, tab="구축방향", source="p.6 · 리스트",
            detail="1) 지식저장소, 그룹웨어, 웹하드 등 산재된 정보 수집을 위한 파이프 라인 구축"),
    ]
    out, n = refine_list_hierarchy(reqs)
    assert n >= 1
    assert len(out) == 1
    assert out[0].top == "지식베이스(RAG)"
    assert "지식저장소" in out[0].mid
    assert out[0].detail.startswith("-")


def test_bullet_carries_mid() -> None:
    reqs = [
        Req(doc="t", table_id=-1, page=1, tab="t", source="p.1 · 리스트",
            detail="가. 테스트 항목"),
        Req(doc="t", table_id=-1, page=1, tab="t", source="p.1 · 리스트",
            detail="1) 중분류 제목입니다"),
        Req(doc="t", table_id=-1, page=1, tab="t", source="p.1 · 리스트",
            detail="- 상세 불릿 한 줄"),
    ]
    out, _ = refine_list_hierarchy(reqs)
    assert len(out) == 2
    assert out[0].mid == "중분류 제목입니다"
    assert not out[1].top and not out[1].mid
    assert out[1].detail == "- 상세 불릿 한 줄"


def test_appendix_same_id_per_table() -> None:
    reqs = [
        Req(doc="t", table_id=3, page=1, tab="부록", top="표#3", mid="a", detail="row1"),
        Req(doc="t", table_id=3, page=1, tab="부록", top="표#3", mid="b", detail="row2"),
        Req(doc="t", table_id=5, page=2, tab="부록", top="표#5", detail="other"),
    ]
    assign_ids(reqs)
    assert reqs[0].rid == reqs[1].rid == "부록_001"
    assert reqs[2].rid == "부록_002"
