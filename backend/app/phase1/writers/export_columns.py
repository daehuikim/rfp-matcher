from __future__ import annotations

from collections.abc import Callable

from app.domain.models import HumanJudgement, Recommendation, Requirement

# cols 쿼리 파라미터 키 → (엑셀 헤더, 행 값 추출)
ExportColumnDef = tuple[str, Callable[[Requirement, Recommendation | None, HumanJudgement | None], str]]

EXPORT_COLUMNS: dict[str, ExportColumnDef] = {
    "category": ("분류", lambda r, _rec, _jud: r.category),
    "code": ("코드", lambda r, _rec, _jud: r.code),
    "name": ("명칭", lambda r, _rec, _jud: r.name),
    "definition": ("정의", lambda r, _rec, _jud: r.definition or ""),
    "detail": ("세부내용", lambda r, _rec, _jud: r.detail),
    "deliverables": ("산출정보", lambda r, _rec, _jud: r.deliverables or ""),
    "related": ("관련요구사항", lambda r, _rec, _jud: ", ".join(r.related)),
    "ai_risk": ("AI 리스크", lambda _r, rec, _jud: rec.ai_risk.value if rec else ""),
    "ai_reason": ("AI 이유", lambda _r, rec, _jud: rec.ai_reason if rec else ""),
    "matched_solutions": (
        "연관 솔루션",
        lambda _r, rec, _jud: ", ".join(rec.matched_solutions) if rec else "",
    ),
    "missing_tech": (
        "부족 기술",
        lambda _r, rec, _jud: ", ".join(rec.missing_tech) if rec else "",
    ),
    "consortium": (
        "필요 컨소시엄",
        lambda _r, rec, _jud: rec.consortium_need or "" if rec else "",
    ),
    "human_mark": ("사람 판정", lambda _r, _rec, jud: jud.mark.value if jud else ""),
    "human_note": ("사람 메모", lambda _r, _rec, jud: jud.note if jud else ""),
}

# PM 성향별 프리셋
EXPORT_PRESETS: dict[str, list[str]] = {
    "original": ["category", "name", "detail"],
    "standard": ["category", "name", "definition", "detail", "deliverables", "related"],
    "full": list(EXPORT_COLUMNS.keys()),
}

DEFAULT_EXPORT_COLUMNS = EXPORT_PRESETS["standard"]


def resolve_export_columns(cols: list[str] | None) -> list[str]:
    if not cols:
        return DEFAULT_EXPORT_COLUMNS
    out: list[str] = []
    for key in cols:
        if key in EXPORT_PRESETS:
            for k in EXPORT_PRESETS[key]:
                if k not in out:
                    out.append(k)
        elif key in EXPORT_COLUMNS and key not in out:
            out.append(key)
    return out or DEFAULT_EXPORT_COLUMNS
