from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook

from app.domain.enums import ExportMode, Judgement
from app.domain.models import ExtractionMetadata, HumanJudgement, Recommendation, Requirement
from app.phase1.extraction.category_provenance import (
    category_source_label,
    document_category_spec,
    summarize_category_sources,
)

from .export_columns import EXPORT_COLUMNS, column_headers, resolve_export_columns

_INVALID_SHEET_CHARS = re.compile(r"[\\/?*\[\]:]")


def _safe_sheet_name(name: str) -> str:
    name = _INVALID_SHEET_CHARS.sub("_", name).strip() or "기타"
    return name[:31]


class RequirementSheetWriter:
    """
    Requirement + Recommendation + HumanJudgement → xlsx.

    cols 파라미터로 출력 칼럼을 선택할 수 있다 (code 등 시스템 생성 필드 제외 가능).
    """

    def write(
        self,
        out_path: Path,
        requirements: list[Requirement],
        recommendations: dict[str, Recommendation] | None = None,
        judgements: dict[str, HumanJudgement] | None = None,
        mode: ExportMode = ExportMode.BOTH,
        columns: list[str] | None = None,
        adaptive: bool = True,
        extraction_meta: ExtractionMetadata | None = None,
    ) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        recs = recommendations or {}
        judges = judgements or {}
        col_keys = resolve_export_columns(
            requirements,
            recs,
            judges,
            mode,
            columns,
            adaptive=adaptive,
        )
        headers = column_headers(col_keys)

        wb = Workbook()
        wb.remove(wb.active)

        by_cat: dict[str, list[Requirement]] = defaultdict(list)
        cat_sources: dict[str, str] = {}
        for r in requirements:
            cat = r.category or "기타"
            by_cat[cat].append(r)
            if cat not in cat_sources:
                cat_sources[cat] = category_source_label(r.category_source)

        summary = wb.create_sheet(title="총괄표")
        summary.append(["내보내기 명세"])
        summary.append(["분류 기준", document_category_spec(extraction_meta, requirements)])
        source_counts = summarize_category_sources(requirements)
        if source_counts:
            summary.append(["분류 출처 집계", ", ".join(f"{k} {v}건" for k, v in source_counts.items())])
        summary.append([])
        show_source_col = "category_source" in col_keys
        summary.append(["분류", "요구사항수"] + (["출처"] if show_source_col else []))
        for cat, items in sorted(by_cat.items()):
            row = [cat, len(items)]
            if show_source_col:
                row.append(cat_sources.get(cat, ""))
            summary.append(row)

        recs = recommendations or {}
        judges = judgements or {}
        for cat, items in sorted(by_cat.items()):
            ws = wb.create_sheet(title=_safe_sheet_name(cat))
            ws.append(headers)
            for r in items:
                rec = recs.get(r.id)
                jud = judges.get(r.id)
                row = self._build_row(r, rec, jud, col_keys, mode)
                ws.append(row)

        wb.save(out_path)
        return out_path

    @staticmethod
    def _build_row(
        req: Requirement,
        rec: Recommendation | None,
        jud: HumanJudgement | None,
        col_keys: list[str],
        mode: ExportMode,
    ) -> list[str]:
        ai_keys = {
            "ai_risk",
            "ai_reason",
            "matched_solutions",
            "missing_tech",
            "consortium",
        }
        human_keys = {"human_mark", "human_note"}
        out: list[str] = []
        for key in col_keys:
            if key in ai_keys and mode == ExportMode.HUMAN:
                out.append("")
                continue
            if key in human_keys and mode == ExportMode.AI:
                out.append("" if key != "human_mark" else Judgement.UNSET.value)
                continue
            out.append(EXPORT_COLUMNS[key].cell_value(req, rec, jud))
        return out
