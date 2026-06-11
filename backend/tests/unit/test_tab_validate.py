from __future__ import annotations

from prototype.v2.extract import Req
from prototype.v2.llm_tabvalidate import _deterministic_drop, validate_tabs_sync


def _req(tab: str, detail: str = "구축하여야 함") -> Req:
    return Req(doc="t", table_id=1, page=1, tab=tab, detail=detail)


def test_keep_proposal_tabs_by_name() -> None:
    for tab in (
        "프로젝트 범위",
        "프로젝트 수행방안",
        "프로젝트 관리 및 품질보증 방안",
        "리스크 관리",
        "기술지원 방안",
        "교육 및 기술이전 방안",
        "제안개요",
    ):
        assert _deterministic_drop(tab) is False


def test_drop_guide_tabs_by_name() -> None:
    assert _deterministic_drop("제안서 작성 기준") is True
    assert _deterministic_drop("서식") is True


def test_validate_preserves_all_hana_like_tabs_without_llm() -> None:
    tabs = [
        "프로젝트 범위",
        "프로젝트 수행방안",
        "프로젝트 관리 및 품질보증 방안",
        "리스크 관리",
        "제안서 작성 기준",
    ]
    reqs = [_req(t) for t in tabs]
    # 제안서 작성 기준 만 결정적 drop — 나머지 전부 유지 (LLM 없이도)
    kept = {r.tab for r in validate_tabs_sync(reqs)}
    assert "프로젝트 관리 및 품질보증 방안" in kept
    assert "리스크 관리" in kept
    assert "제안서 작성 기준" not in kept
