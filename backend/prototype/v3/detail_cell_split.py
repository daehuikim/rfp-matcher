"""
상세 셀 atomic 분해 — 표마다 LLM 스키마 추론 + 셀별 분할.

문서마다 불릿·번호 체계가 달라 하드코딩 대신 TableSplitProfiler(phase1)를 재사용한다.
API 키 없거나 use_llm=False 이면 split_by_markers 룰만 사용.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.config import Settings
from app.llm.factory import build_llm_client
from app.llm.fake_client import FakeLlmClient
from app.phase1.extraction.parsing import (
    Atom,
    CIRCLED_DIGITS,
    has_primary_marker_structure,
    normalize_line_breaks,
    split_by_markers,
)
from app.phase1.extraction.table_split_profiler import SplittingSchema, TableSplitProfiler
from prototype.v2.async_run import run_coro
from prototype.v2.text import norm_lines

logger = logging.getLogger(__name__)

_DEFAULT_HEADER = ["요건 구분", "상세내용"]


def atom_to_text(atom: Atom) -> str:
    if atom.marker:
        return norm_lines(f"{atom.marker} {atom.text}".strip())
    return norm_lines(atom.text.strip())


def atoms_to_texts(atoms: list[Atom]) -> list[str]:
    return [t for a in atoms if (t := atom_to_text(a))]


def _prepare_cell_text(text: str, schema: SplittingSchema) -> str:
    """PDF/HTML 인라인 마커를 줄 단위로 복원 — split_by_markers 입력 정규화."""
    text = normalize_line_breaks(norm_lines(text))
    markers = [m for m in schema.primary_markers if m and m.strip()]
    if not markers:
        text = re.sub(rf"(?<!\n)([{CIRCLED_DIGITS}])", r"\n\1", text)
    else:
        for m in markers:
            text = re.sub(rf"(?<!\n){re.escape(m)}", f"\n{m}", text)
    return text


@dataclass
class DetailCellSplitter:
    """표 단위 스키마 캐시 + 셀 분해."""

    use_llm: bool = True
    _profiler: TableSplitProfiler | None = field(default=None, repr=False)
    _schemas: dict[int, SplittingSchema] = field(default_factory=dict)
    _samples: dict[int, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.use_llm:
            s = Settings()
            client = build_llm_client(s)
            if isinstance(client, FakeLlmClient):
                logger.info("detail_cell_split: LLM unavailable — 룰 분해만 사용")
                self.use_llm = False
            else:
                self._profiler = TableSplitProfiler(
                    client,
                    use_llm=True,
                    concurrency=max(2, s.llm_concurrency),
                )

    def note_sample(self, table_id: int, detail: str) -> None:
        text = norm_lines(detail)
        if not text:
            return
        bucket = self._samples.setdefault(table_id, [])
        if text not in bucket and len(bucket) < 5:
            bucket.append(text)

    def _schema(self, table_id: int, header: list[str] | None = None) -> SplittingSchema:
        if table_id in self._schemas:
            return self._schemas[table_id]
        hdr = header or _DEFAULT_HEADER
        samples = self._samples.get(table_id, [])
        if self._profiler and samples:
            schema = run_coro(
                self._profiler.infer_schema(
                    table_index=table_id,
                    header=hdr,
                    sample_details=samples,
                )
            )
        else:
            schema = SplittingSchema(group_sub_bullets=True)
        self._schemas[table_id] = schema
        return schema

    def split_cell(
        self,
        detail: str,
        *,
        table_id: int,
        group: str = "",
        header: list[str] | None = None,
    ) -> list[str]:
        """셀 본문 → 조견표 1행 단위 텍스트 목록."""
        raw = norm_lines(detail)
        if not raw:
            return []
        self.note_sample(table_id, raw)
        schema = self._schema(table_id, header)
        text = _prepare_cell_text(raw, schema)

        if self._profiler:
            atoms = run_coro(
                self._profiler.split_cell(text, schema, category_raw=group or None)
            )
        else:
            atoms = split_by_markers(text, extra_markers=schema.primary_markers)
            if not atoms:
                atoms = [Atom(marker=None, text=text)]

        return atoms_to_texts(atoms)

    def is_continuation_only(self, detail: str, *, table_id: int, header: list[str] | None = None) -> bool:
        """상위 번호 없이 이어지는 불릿 행인지 — 직전 셀에 병합 후보."""
        raw = norm_lines(detail)
        if not raw:
            return False
        schema = self._schema(table_id, header)
        text = _prepare_cell_text(raw, schema)
        atoms = split_by_markers(text, extra_markers=schema.primary_markers)
        if has_primary_marker_structure(atoms):
            return False
        return len(atoms) <= 1


def make_splitter(*, use_llm: bool = True) -> DetailCellSplitter:
    return DetailCellSplitter(use_llm=use_llm)
