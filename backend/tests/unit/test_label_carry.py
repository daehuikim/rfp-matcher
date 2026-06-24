from __future__ import annotations

from prototype.v2.extract import Req
from prototype.v2.label_carry import carry_forward_hierarchy


def test_carry_forward_fills_page_break_gap() -> None:
    reqs = [
        Req(doc="d", table_id=1, page=1, tab="탭A", top="항목1", mid="요구A", detail="d1"),
        Req(doc="d", table_id=1, page=2, tab="탭A", top="", mid="", detail="d2"),
    ]
    out, n = carry_forward_hierarchy(reqs)
    assert n == 2
    assert out[1].top == "항목1"
    assert out[1].mid == "요구A"
    assert not out[1].gen_top and not out[1].gen_mid


def test_new_top_resets_mid_carry() -> None:
    reqs = [
        Req(doc="d", table_id=1, page=1, tab="T", top="A", mid="M1", detail="x"),
        Req(doc="d", table_id=1, page=1, tab="T", top="B", mid="", detail="y"),
    ]
    out, _ = carry_forward_hierarchy(reqs)
    assert out[1].mid == ""
