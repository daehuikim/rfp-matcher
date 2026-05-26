from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.domain.enums import ExportMode
from app.domain.models import HumanJudgement, Recommendation, Requirement
from app.phase1.extraction.category_provenance import (
    category_source_label,
    has_inferred_categories,
    subcategory_has_meaningful_data,
)

RowGetter = Callable[[Requirement, Recommendation | None, HumanJudgement | None], str]

UNKNOWN_GROUP = "미분류"


@dataclass(frozen=True)
class ExportColumn:
    """내보내기 칼럼 정의 — 헤더는 문서 종류 중립적, 포함 여부는 데이터 기준."""

    key: str
    header: str
    getter: RowGetter
    group: str  # requirement | ai | human
    always: bool = False  # adaptive 모드에서도 항상 포함

    def cell_value(
        self,
        req: Requirement,
        rec: Recommendation | None,
        jud: HumanJudgement | None,
    ) -> str:
        return self.getter(req, rec, jud)

    def has_data(
        self,
        requirements: Sequence[Requirement],
        recommendations: dict[str, Recommendation],
        judgements: dict[str, HumanJudgement],
    ) -> bool:
        if self.always:
            return True
        for req in requirements:
            rec = recommendations.get(req.id)
            jud = judgements.get(req.id)
            if self.cell_value(req, rec, jud).strip():
                return True
        return False

    def allowed_for_mode(self, mode: ExportMode) -> bool:
        if self.group == "ai" and mode == ExportMode.HUMAN:
            return False
        if self.group == "human" and mode == ExportMode.AI:
            return False
        return True

    def is_applicable(
        self,
        requirements: Sequence[Requirement],
        recommendations: dict[str, Recommendation],
        judgements: dict[str, HumanJudgement],
        mode: ExportMode,
    ) -> bool:
        if not self.allowed_for_mode(mode):
            return False
        if self.key == "category":
            return any(
                (r.category or "").strip() and r.category != UNKNOWN_GROUP for r in requirements
            )
        if self.key == "subcategory":
            return subcategory_has_meaningful_data(requirements)
        if self.key == "category_source":
            return has_inferred_categories(requirements)
        return self.has_data(requirements, recommendations, judgements)


EXPORT_COLUMNS: dict[str, ExportColumn] = {
    "category": ExportColumn(
        key="category",
        header="분류",
        getter=lambda r, _rec, _jud: r.category,
        group="requirement",
    ),
    "subcategory": ExportColumn(
        key="subcategory",
        header="소분류",
        getter=lambda r, _rec, _jud: r.subcategory or "",
        group="requirement",
    ),
    "code": ExportColumn(
        key="code",
        header="코드",
        getter=lambda r, _rec, _jud: r.code,
        group="requirement",
    ),
    "name": ExportColumn(
        key="name",
        header="명칭",
        getter=lambda r, _rec, _jud: r.name,
        group="requirement",
    ),
    "definition": ExportColumn(
        key="definition",
        header="정의",
        getter=lambda r, _rec, _jud: r.definition or "",
        group="requirement",
    ),
    "detail": ExportColumn(
        key="detail",
        header="세부내용",
        getter=lambda r, _rec, _jud: r.detail,
        group="requirement",
        always=True,
    ),
    "source_page": ExportColumn(
        key="source_page",
        header="원본 페이지",
        getter=lambda r, _rec, _jud: str(r.source_page) if r.source_page else "",
        group="requirement",
    ),
    "source_section": ExportColumn(
        key="source_section",
        header="원본 섹션",
        getter=lambda r, _rec, _jud: r.source_section or "",
        group="requirement",
    ),
    "deliverables": ExportColumn(
        key="deliverables",
        header="산출정보",
        getter=lambda r, _rec, _jud: r.deliverables or "",
        group="requirement",
    ),
    "related": ExportColumn(
        key="related",
        header="관련요구사항",
        getter=lambda r, _rec, _jud: ", ".join(r.related),
        group="requirement",
    ),
    "ai_risk": ExportColumn(
        key="ai_risk",
        header="AI 리스크",
        getter=lambda _r, rec, _jud: rec.ai_risk.value if rec else "",
        group="ai",
    ),
    "ai_reason": ExportColumn(
        key="ai_reason",
        header="AI 이유",
        getter=lambda _r, rec, _jud: rec.ai_reason if rec else "",
        group="ai",
    ),
    "matched_solutions": ExportColumn(
        key="matched_solutions",
        header="연관 솔루션",
        getter=lambda _r, rec, _jud: ", ".join(rec.matched_solutions) if rec else "",
        group="ai",
    ),
    "missing_tech": ExportColumn(
        key="missing_tech",
        header="부족 기술",
        getter=lambda _r, rec, _jud: ", ".join(rec.missing_tech) if rec else "",
        group="ai",
    ),
    "consortium": ExportColumn(
        key="consortium",
        header="필요 컨소시엄",
        getter=lambda _r, rec, _jud: rec.consortium_need or "" if rec else "",
        group="ai",
    ),
    "human_mark": ExportColumn(
        key="human_mark",
        header="사람 판정",
        getter=lambda _r, _rec, jud: jud.mark.value if jud else "",
        group="human",
    ),
    "human_note": ExportColumn(
        key="human_note",
        header="사람 메모",
        getter=lambda _r, _rec, jud: jud.note if jud else "",
        group="human",
    ),
    "category_source": ExportColumn(
        key="category_source",
        header="분류 출처",
        getter=lambda r, _rec, _jud: category_source_label(r.category_source),
        group="requirement",
    ),
}

# 프리셋 = 후보 키 순서. adaptive 해석 시 데이터 없는 칼럼은 자동 제외.
EXPORT_PRESETS: dict[str, list[str]] = {
    "original": ["category", "subcategory", "name", "detail"],
    "standard": [
        "category",
        "subcategory",
        "name",
        "definition",
        "detail",
        "source_page",
        "source_section",
        "deliverables",
        "related",
        "ai_risk",
        "ai_reason",
        "matched_solutions",
        "human_mark",
        "human_note",
        "category_source",
    ],
    "full": list(EXPORT_COLUMNS.keys()),
}

DEFAULT_PRESET = "standard"


def expand_column_keys(requested: Sequence[str] | None) -> list[str]:
    if not requested:
        return list(EXPORT_PRESETS[DEFAULT_PRESET])
    out: list[str] = []
    for key in requested:
        if key in EXPORT_PRESETS:
            for k in EXPORT_PRESETS[key]:
                if k not in out:
                    out.append(k)
        elif key in EXPORT_COLUMNS and key not in out:
            out.append(key)
    return out or list(EXPORT_PRESETS[DEFAULT_PRESET])


def list_applicable_columns(
    requirements: Sequence[Requirement],
    recommendations: dict[str, Recommendation],
    judgements: dict[str, HumanJudgement],
    mode: ExportMode,
) -> list[str]:
    """문서·모드 기준으로 값이 있거나 항상 포함되는 칼럼 키."""
    return [
        col.key
        for col in EXPORT_COLUMNS.values()
        if col.is_applicable(requirements, recommendations, judgements, mode)
    ]


def resolve_export_columns(
    requirements: Sequence[Requirement],
    recommendations: dict[str, Recommendation],
    judgements: dict[str, HumanJudgement],
    mode: ExportMode,
    columns: Sequence[str] | None = None,
    *,
    adaptive: bool = True,
) -> list[str]:
    """
    내보낼 칼럼 키 목록.

    adaptive=True(기본): 프리셋/요청 목록 중 실제 데이터가 있는 칼럼만 포함.
    adaptive=False: 요청한 키를 그대로 사용 (레거시·테스트용).
    """
    candidates = expand_column_keys(columns)
    if not adaptive:
        return [k for k in candidates if k in EXPORT_COLUMNS]

    applicable = set(list_applicable_columns(requirements, recommendations, judgements, mode))
    out = [k for k in candidates if k in applicable]
    if out:
        return out
    if "detail" in applicable:
        return ["detail"]
    return [next(iter(applicable))] if applicable else ["detail"]


def column_headers(keys: Sequence[str]) -> list[str]:
    return [EXPORT_COLUMNS[k].header for k in keys]
