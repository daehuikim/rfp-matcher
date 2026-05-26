from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook

from app.domain.enums import ExportMode, Judgement
from app.domain.models import HumanJudgement, Recommendation, Requirement

from .export_columns import EXPORT_COLUMNS, resolve_export_columns

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
    ) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        col_keys = resolve_export_columns(columns)
        headers = [EXPORT_COLUMNS[k][0] for k in col_keys]

        wb = Workbook()
        wb.remove(wb.active)

        by_cat: dict[str, list[Requirement]] = defaultdict(list)
        for r in requirements:
            by_cat[r.category or "기타"].append(r)

        summary = wb.create_sheet(title="총괄표")
        summary.append(["요청사항 구분", "요구사항수"])
        for cat, items in sorted(by_cat.items()):
            summary.append([cat, len(items)])

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
            _, getter = EXPORT_COLUMNS[key]
            out.append(getter(req, rec, jud))
        return out
