from __future__ import annotations

import pytest

from app.domain.enums import ExportMode, Judgement
from app.domain.models import HumanJudgement, Recommendation, Requirement
from app.phase1.writers.export_columns import (
    EXPORT_PRESETS,
    list_applicable_columns,
    resolve_export_columns,
)


def _req(**kwargs) -> Requirement:
    base = dict(
        id="r1",
        doc_id="d",
        category="데이터 수집",
        code="DATA-001",
        name="원천 연계",
        detail="원천 시스템 연계 상세",
    )
    base.update(kwargs)
    return Requirement(**base)


def test_adaptive_standard_skips_empty_optional_fields() -> None:
    reqs = [_req()]
    keys = resolve_export_columns(reqs, {}, {}, ExportMode.BOTH, EXPORT_PRESETS["standard"])
    assert "detail" in keys
    assert "category" in keys
    assert "definition" not in keys
    assert "deliverables" not in keys
    assert "related" not in keys


def test_adaptive_includes_subcategory_when_present() -> None:
    reqs = [_req(subcategory="검색·포털")]
    keys = resolve_export_columns(reqs, {}, {}, ExportMode.BOTH, EXPORT_PRESETS["original"])
    assert "subcategory" in keys


def test_adaptive_skips_category_when_only_unknown_group() -> None:
    reqs = [_req(category="미분류")]
    keys = resolve_export_columns(reqs, {}, {}, ExportMode.BOTH, EXPORT_PRESETS["original"])
    assert "category" not in keys
    assert "detail" in keys


def test_adaptive_includes_ai_columns_when_recommendations_exist() -> None:
    reqs = [_req()]
    recs = {
        "r1": Recommendation(
            requirement_id="r1",
            ai_risk=Judgement.YES,
            ai_reason="가능",
        )
    }
    keys = resolve_export_columns(reqs, recs, {}, ExportMode.BOTH, EXPORT_PRESETS["standard"])
    assert "ai_risk" in keys
    assert "ai_reason" in keys


def test_adaptive_human_mode_excludes_ai_columns() -> None:
    reqs = [_req()]
    recs = {"r1": Recommendation(requirement_id="r1", ai_risk=Judgement.YES, ai_reason="x")}
    keys = list_applicable_columns(reqs, recs, {}, ExportMode.HUMAN)
    assert "ai_risk" not in keys
    assert "human_mark" in keys or "detail" in keys


def test_non_adaptive_keeps_requested_keys() -> None:
    reqs = [_req()]
    keys = resolve_export_columns(
        reqs,
        {},
        {},
        ExportMode.BOTH,
        ["definition", "detail"],
        adaptive=False,
    )
    assert keys == ["definition", "detail"]
