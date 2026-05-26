from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.llm.base import AsyncLlmClient, Message

from .parsing import Atom, has_primary_marker_structure, split_by_markers
from .split_prompts import (
    ATOMIC_UNIT_RULES,
    SPLIT_CELL_FEW_SHOT,
    SPLIT_SCHEMA_FEW_SHOT,
)

if TYPE_CHECKING:
    from app.llm.usage import LlmUsageTracker

logger = logging.getLogger(__name__)


class SplittingSchema(BaseModel):
    """표 단위 atomic 분해 규칙 — LLM이 샘플 본문을 보고 추론."""

    primary_markers: list[str] = Field(default_factory=list)
    split_on_blank_line: bool = False
    one_row_one_requirement: bool = False
    group_sub_bullets: bool = True
    notes: str = ""


class _LlmSplittingSchemaOut(BaseModel):
    primary_markers: list[str] = Field(default_factory=list)
    split_on_blank_line: bool = False
    one_row_one_requirement: bool = False
    group_sub_bullets: bool = True
    notes: str = ""


class _LlmAtoms(BaseModel):
    atoms: list[str]


class TableSplitProfiler:
    """
    조견표(TableRef)마다 분해 스키마를 LLM으로 추론하고, 각 셀을 atomic 단위로 분해.

    ① 아래 • 불릿은 한 atom — LLM 과분할 방지를 위해 룰 결과를 우선 신뢰.
    """

    def __init__(
        self,
        llm: AsyncLlmClient,
        *,
        use_llm: bool = True,
        concurrency: int = 8,
        tracker: LlmUsageTracker | None = None,
    ) -> None:
        self._llm = llm
        self._use_llm = use_llm
        self._sem = asyncio.Semaphore(concurrency)
        self._tracker = tracker

    async def infer_schema(
        self,
        *,
        table_index: int,
        header: list[str],
        sample_details: list[str],
    ) -> SplittingSchema:
        if not self._use_llm or not sample_details:
            return SplittingSchema(group_sub_bullets=True)

        samples_block = "\n\n".join(
            f"--- sample {i + 1} ---\n{text[:900]}" for i, text in enumerate(sample_details[:5])
        )
        prompt = (
            f"RFP 조견표(table_index={table_index})에서 PM 검토용 'atomic 요구사항 1건' 경계 규칙을 추론하라.\n\n"
            f"{ATOMIC_UNIT_RULES}\n"
            f"{SPLIT_SCHEMA_FEW_SHOT}\n\n"
            f"[헤더]\n{header}\n\n"
            f"[샘플 상세 셀]\n{samples_block}\n\n"
            "JSON:\n"
            '{"primary_markers": ["①", "②", ...], '
            '"split_on_blank_line": false, '
            '"one_row_one_requirement": false, '
            '"group_sub_bullets": true, '
            '"notes": "한국어 1~2문장"}'
        )
        try:
            async with self._sem:
                out = await self._llm.structured_output(
                    [Message(role="user", content=prompt)],
                    _LlmSplittingSchemaOut,
                    purpose=f"split_schema:table{table_index}",
                    tracker=self._tracker,
                )
            return SplittingSchema.model_validate(out.model_dump())
        except Exception as e:  # noqa: BLE001
            logger.warning("표 분해 스키마 추론 실패 table=%d: %s", table_index, e)
            return SplittingSchema(group_sub_bullets=True)

    async def split_cell(
        self,
        cell: str,
        schema: SplittingSchema,
        *,
        category_raw: str | None = None,
    ) -> list[Atom]:
        rule_atoms = split_by_markers(cell, extra_markers=schema.primary_markers)

        # ①②③ + 하위 • 가 이미 묶인 경우 — LLM 재분해 스킵 (과분할 방지)
        if has_primary_marker_structure(rule_atoms):
            return rule_atoms

        if not self._use_llm:
            return rule_atoms if rule_atoms else [Atom(marker=None, text=cell.strip())]

        hint = "\n".join(
            f"- {a.marker or '·'} {a.text[:120]}" for a in rule_atoms[:12]
        ) or "(룰 분해 결과 없음 — 셀 전체가 한 덩어리)"
        cat_line = f"행 분류: {category_raw}\n" if category_raw else ""
        prompt = (
            "RFP 조견표 상세 셀을 PM 검토용 '요구사항 1건' 단위로 분해하라.\n\n"
            f"{ATOMIC_UNIT_RULES}\n"
            f"{SPLIT_CELL_FEW_SHOT}\n\n"
            f"{cat_line}"
            f"[표 분해 스키마]\n"
            f"primary_markers: {schema.primary_markers}\n"
            f"group_sub_bullets: {schema.group_sub_bullets}\n"
            f"notes: {schema.notes}\n\n"
            f"[룰 기반 1차 분해 — 참고용]\n{hint}\n\n"
            f"[셀 본문]\n{cell}\n\n"
            '응답 JSON: {"atoms": ["...", ...]}'
        )
        try:
            async with self._sem:
                result = await self._llm.structured_output(
                    [Message(role="user", content=prompt)],
                    _LlmAtoms,
                    purpose="split_cell",
                    tracker=self._tracker,
                )
            texts = [t.strip() for t in result.atoms if t.strip()]
            if texts:
                return [Atom(marker=None, text=t) for t in texts]
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 셀 분해 실패, 룰 결과 유지: %s", e)

        if rule_atoms:
            return rule_atoms
        stripped = cell.strip()
        return [Atom(marker=None, text=stripped)] if stripped else []
