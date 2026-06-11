from __future__ import annotations

from prototype.v2.domain_topics import _blocks_for_tab, _deterministic_coarsen_tab, infer_domain
from prototype.v2.extract import Req
from prototype.v2.ids import assign_ids, domain_slug


def test_infer_domain_tab_first() -> None:
    assert infer_domain("임의 본문", "리스크 관리") == "리스크 관리"


def test_infer_domain_unstructured_platform() -> None:
    assert infer_domain("비정형 데이터 파싱 및 VectorDB") == "비정형 데이터 플랫폼"


def test_domain_slug_aliases() -> None:
    assert domain_slug("지식베이스(RAG)") == "지식베이스"
    assert domain_slug("AI Agent 플랫폼") == "Agent플랫폼"


def test_deterministic_coarsen_merges_micro_tops() -> None:
    reqs = [
        Req(doc="t", table_id=1, page=1, tab="T", top="백업 및 복구", mid="m1", detail="- a"),
        Req(doc="t", table_id=1, page=1, tab="T", top="", mid="", detail="- b"),
        Req(doc="t", table_id=1, page=1, tab="T", top="서버구성", mid="m2", detail="- c"),
    ]
    idxs = [0, 1, 2]
    blocks = _blocks_for_tab(reqs, idxs)
    n = _deterministic_coarsen_tab(reqs, idxs, blocks)
    assert n >= 2
    assert reqs[0].top in ("시스템 일반", "프로젝트 범위")
    assert not reqs[1].top
    assign_ids(reqs)
    assert reqs[0].rid.startswith("시스템_")
    assert reqs[2].rid.startswith("시스템_")
