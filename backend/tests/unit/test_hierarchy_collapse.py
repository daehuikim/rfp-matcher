from __future__ import annotations

from prototype.v2.extract import Req
from prototype.v2.hierarchy_collapse import (
    collapse_bullet_groups,
    promote_table_mid_anchors,
    refine_hierarchy,
    split_merged_top_mid,
)
from prototype.v2.ids import assign_ids


def test_split_merged_top_mid() -> None:
    r = Req(doc="t", table_id=1, page=1, tab="T",
            top="지식베이스(RAG) / 지식 수집 및 처리", mid="", detail="- 본문")
    assert split_merged_top_mid(r)
    assert r.top == "지식베이스(RAG)"
    assert r.mid == "지식 수집 및 처리"


def test_promote_table_bullet_group_gold_style() -> None:
    reqs = [
        Req(doc="t", table_id=633, page=18, tab="프로젝트",
            top="지식베이스(RAG)", mid="지식 수집 및 처리",
            detail="- 파이프라인 A", source="p.18 · 표#633"),
        Req(doc="t", table_id=633, page=18, tab="프로젝트",
            top="", mid="",
            detail="- 파이프라인 B", source="p.18 · 표#633"),
        Req(doc="t", table_id=633, page=18, tab="프로젝트",
            top="지식베이스(RAG)", mid="데이터 가공 및 저장",
            detail="- Vector DB", source="p.18 · 표#633"),
    ]
    from prototype.v2.hierarchy_collapse import promote_table_mid_anchors
    _, n = promote_table_mid_anchors(reqs)
    assert n >= 1
    assert reqs[0].top == "지식베이스(RAG)" and reqs[0].mid == "지식 수집 및 처리"
    assert not reqs[1].top and not reqs[1].mid
    assert reqs[2].mid == "데이터 가공 및 저장"


def test_assign_ids_global_unique() -> None:
    reqs = [
        Req(doc="t", table_id=-1, page=1, tab="구축방향", top="지식베이스(RAG)", mid="m", detail="a"),
        Req(doc="t", table_id=1, page=18, tab="프로젝트", top="지식베이스(RAG)", mid="m", detail="b"),
        Req(doc="t", table_id=1, page=18, tab="프로젝트", top="", mid="m2", detail="c"),
    ]
    assign_ids(reqs)
    assert reqs[0].rid == "지식베이스_001"
    assert reqs[1].rid == "지식베이스_002"
    assert reqs[2].rid == "지식베이스_003"
