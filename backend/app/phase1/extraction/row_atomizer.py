from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup, Tag

from app.domain.models import AtomicRow, TableRef
from app.llm.base import AsyncLlmClient

from .parsing import split_by_markers
from .table_columns import (
    detect_column_indices,
    extract_category_and_detail,
    first_row_is_content,
    infer_continuation_columns,
    is_short_header_row,
    is_valid_category_label,
    normalize_category_label,
    row_has_requirement_body,
)
from .table_split_profiler import SplittingSchema, TableSplitProfiler

if TYPE_CHECKING:
    from app.llm.usage import LlmUsageTracker

logger = logging.getLogger(__name__)


class RowAtomizer:
    """
    조견표 한 행(또는 셀)을 atomic 단위로 분해.

    절차:
      1) 헤더에서 '요건 구분'·'상세내용' 열 위치 파악
      2) 표 샘플로 LLM 분해 스키마 추론 (TableSplitProfiler)
      3) 각 행 상세 셀 → LLM atomic 분해 (룰은 힌트)
    """

    def __init__(
        self,
        llm: AsyncLlmClient,
        use_llm: bool = True,
        llm_fallback: bool | None = None,
        llm_concurrency: int = 8,
        tracker: LlmUsageTracker | None = None,
    ) -> None:
        if llm_fallback is not None:
            use_llm = llm_fallback
        self._profiler = TableSplitProfiler(
            llm, use_llm=use_llm, concurrency=llm_concurrency, tracker=tracker
        )

    async def atomize(
        self,
        doc_id: str,
        html_path: Path,
        table_ref: TableRef,
        *,
        carry_category: str | None = None,
    ) -> tuple[list[AtomicRow], str | None]:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
        tables = soup.find_all("table")
        if table_ref.table_index >= len(tables):
            logger.warning("table_index 범위 초과: %d / %d", table_ref.table_index, len(tables))
            return [], carry_category
        tbl = tables[table_ref.table_index]
        if not isinstance(tbl, Tag):
            return [], carry_category
        rows = tbl.find_all("tr")
        if not rows:
            return [], carry_category

        first_cells = rows[0].find_all(["td", "th"]) if isinstance(rows[0], Tag) else []
        header_texts = [c.get_text(strip=True) for c in first_cells]

        if table_ref.category_col_index is not None and table_ref.detail_col_index is not None:
            category_col, detail_col = table_ref.category_col_index, table_ref.detail_col_index
        elif header_texts and not first_row_is_content(header_texts):
            category_col, detail_col = detect_column_indices(header_texts)
        else:
            texts = [c.get_text(strip=True) for c in first_cells]
            category_col, detail_col = infer_continuation_columns(texts)

        skip_first = bool(header_texts) and is_short_header_row(header_texts)
        start = 1 if skip_first else 0

        row_payloads: list[tuple[str | None, str]] = []
        seen_detail: set[str] = set()
        sample_details: list[str] = []
        last_valid_category: str | None = (
            carry_category if carry_category and is_valid_category_label(carry_category) else None
        )

        for tr in rows[start:]:
            if not isinstance(tr, Tag):
                continue
            cells = tr.find_all(["td", "th"])
            if not cells or not row_has_requirement_body(cells):
                continue
            category_raw, detail = extract_category_and_detail(
                cells,
                category_col=category_col,
                detail_col=detail_col,
            )
            if category_raw and is_valid_category_label(category_raw):
                last_valid_category = category_raw
            elif last_valid_category:
                category_raw = last_valid_category
            else:
                category_raw = normalize_category_label(category_raw) or "미분류"
            if len(detail.strip()) < 10:
                continue
            norm = detail.strip()
            if norm in seen_detail:
                continue
            seen_detail.add(norm)
            row_payloads.append((category_raw, detail))
            if len(sample_details) < 5:
                sample_details.append(detail)

        schema = await self._profiler.infer_schema(
            table_index=table_ref.table_index,
            header=header_texts or table_ref.header_columns,
            sample_details=sample_details,
        )

        out: list[AtomicRow] = []
        row_tasks = [
            asyncio.create_task(
                self._atomize_row(
                    doc_id=doc_id,
                    table_index=table_ref.table_index,
                    category_raw=category_raw,
                    detail=detail,
                    schema=schema,
                    source_page=table_ref.source_page,
                    source_section=table_ref.section_heading,
                )
            )
            for category_raw, detail in row_payloads
        ]
        if row_tasks:
            for part in await asyncio.gather(*row_tasks):
                out.extend(part)
        return out, last_valid_category

    async def _atomize_row(
        self,
        *,
        doc_id: str,
        table_index: int,
        category_raw: str | None,
        detail: str,
        schema: SplittingSchema,
        source_page: int | None = None,
        source_section: str | None = None,
    ) -> list[AtomicRow]:
        atoms = await self._profiler.split_cell(detail, schema, category_raw=category_raw)
        return [
            AtomicRow(
                doc_id=doc_id,
                table_index=table_index,
                source_cell=detail,
                bullet_marker=atom.marker,
                text=atom.text,
                row_seq=seq,
                category_raw=category_raw,
                source_page=source_page,
                source_section=source_section,
            )
            for seq, atom in enumerate(atoms)
        ]


class ParagraphAtomizer:
    """
    표가 없는(예: 법제처 HWPX) 단락 기반 문서를 위한 폴백.

    단순 휴리스틱: `<p>` 단락을 그대로 atomic 단위로 본다. category는 상위 헤딩(h1~h4)에서 추정.
    """

    async def atomize(self, doc_id: str, html_path: Path) -> list[AtomicRow]:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
        atoms: list[AtomicRow] = []
        current_category: str | None = None
        for node in soup.find_all(["h1", "h2", "h3", "h4", "p"]):
            if not isinstance(node, Tag):
                continue
            text = node.get_text(strip=True)
            if not text:
                continue
            if node.name.startswith("h"):
                current_category = text
                continue
            for seq, atom in enumerate(split_by_markers(text)):
                if not atom.text:
                    continue
                atoms.append(
                    AtomicRow(
                        doc_id=doc_id,
                        table_index=None,
                        source_cell=text,
                        bullet_marker=atom.marker,
                        text=atom.text,
                        row_seq=seq,
                        category_raw=current_category,
                    )
                )
        return atoms
