from __future__ import annotations

from prototype.v2.extract import Req
from prototype.v2.hierarchy_collapse import enforce_gold_spacing


def test_enforce_clears_bullet_continuation() -> None:
    reqs = [
        Req(doc="t", table_id=1, page=1, tab="T", top="도메인", mid="서브", detail="- a"),
        Req(doc="t", table_id=1, page=1, tab="T", top="도메인", mid="서브", detail="- b"),
        Req(doc="t", table_id=1, page=1, tab="T", top="도메인", mid="서브2", detail="- c"),
    ]
    _, n = enforce_gold_spacing(reqs)
    assert n >= 1
    assert reqs[0].top == "도메인" and reqs[0].mid == "서브"
    assert not reqs[1].top and not reqs[1].mid
    assert reqs[2].mid == "서브2"
