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
from .sheet_style import style_data_sheet, write_overview_sheet

_INVALID_SHEET_CHARS = re.compile(r"[\\/?*\[\]:]")


def _safe_sheet_name(name: str) -> str:
    name = _INVALID_SHEET_CHARS.sub("_", name).strip() or "기타"
    return name[:31]


def _document_order_key(r: Requirement) -> tuple[int, int, str]:
    """RFP 원문 순서 정렬 키 — 페이지 → 표 인덱스 → 코드."""
    page = r.source_page
    try:
        page_i = int(page) if page is not None else 10**6
    except (TypeError, ValueError):
        page_i = 10**6
    table_i = r.source_table_index if isinstance(r.source_table_index, int) else 10**6
    return (page_i, table_i, r.code or "")


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
        layout: str = "cluster",
    ) -> Path:
        """layout: "cluster"=분류별 시트(기술 중심) / "ordered"=RFP 원문 순서 단일 시트."""
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

        overview = wb.create_sheet(title="개요")
        write_overview_sheet(
            overview,
            requirements,
            recs,
            by_cat,
            document_category_spec(extraction_meta, requirements),
        )

        recs = recommendations or {}
        judges = judgements or {}

        if layout == "ordered":
            # RFP 원문 순서(페이지→표→코드) 단일 시트
            ordered = sorted(requirements, key=_document_order_key)
            ws = wb.create_sheet(title="요구사항(원문순서)")
            ws.append(headers)
            for r in ordered:
                ws.append(self._build_row(r, recs.get(r.id), judges.get(r.id), col_keys, mode))
            style_data_sheet(ws, col_keys, len(ordered))
        else:
            # 분류별 시트(기술 중심)
            for cat, items in sorted(by_cat.items()):
                ws = wb.create_sheet(title=_safe_sheet_name(cat))
                ws.append(headers)
                for r in items:
                    ws.append(self._build_row(r, recs.get(r.id), judges.get(r.id), col_keys, mode))
                style_data_sheet(ws, col_keys, len(items))

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
