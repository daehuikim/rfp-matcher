from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from typing import TYPE_CHECKING

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel

from app.domain.models import TableRef
from app.llm.base import AsyncLlmClient, Message

from .table_columns import (
    _header_triggers_table_stop,
    detect_column_indices,
    header_is_requirement_table,
    header_looks_like_requirements,
    is_administrative_table_header,
    is_short_header_row,
    table_looks_like_requirement_continuation,
)
from app.phase1.extraction.fallback.section_patterns import (
    DETAIL_REQUIREMENT_CONTENT_ANCHOR,
    DETAIL_REQUIREMENT_CONTENT_TITLE,
    MAJOR_SECTION_RE,
    NON_REQUIREMENT_SECTION_KEYWORDS,
    REQUIREMENT_SECTION_KEYWORDS,
    format_section_heading,
)

if TYPE_CHECKING:
    from app.llm.usage import LlmUsageTracker

logger = logging.getLogger(__name__)

# 조견표 헤더에 자주 등장하는 한국어 키워드 (휴리스틱·LLM 프롬프트 참고용)
HEADER_KEYWORDS = {
    "요건",
    "요건구분",
    "요건 구분",
    "구분",
    "구 분",
    "상세내용",
    "상세 내용",
    "내용",
    "요구사항",
    "요구사항 상세",
    "구축 범위",
    "분류",
    "명칭",
    "설명",
    "정의",
    "항목",
    "요구",
    "기능",
    "성능",
}


@dataclass
class _Candidate:
    index: int  # html 내 table 태그 순번
    header: list[str]
    row_count: int
    col_count: int
    keyword_hits: int = 0
    keyword_ratio: float = 0.0
    sample_rows: list[list[str]] | None = None
    section_heading: str | None = None
    source_page: int | None = None
    in_requirement_zone: bool = False


@dataclass(frozen=True)
class _LocatedMeta:
    located_via: str
    confidence: float


class _LlmVerdict(BaseModel):
    is_requirements_table: bool
    confidence: float


class _BatchItem(BaseModel):
    table_index: int
    is_requirements_table: bool
    confidence: float


class _LlmBatchVerdict(BaseModel):
    results: list[_BatchItem]


def _format_section_heading(match: re.Match[str]) -> str:
    return format_section_heading(match)


class TableLocator:
    """
    HTML 문서에서 조견표 위치를 탐지한다.

    PM 검토용 — **정밀도·재현(recall within zone) 균형**:
    1) 명시적 조견표 헤더(요건 구분+상세내용) + 연속 표 확장
    2) 「(2) 상세 요구사항 내용」 등 상세 구역 내 — **헤더가 조견표일 때만**
    3) 헤더에 요구·요청·상세 키워드가 있는 표 (본문 휴리스틱 단독 제외)
    4) 헤더가 조견표인 미확정 후보만 LLM batch (HW·역할 표 LLM recall 차단)
    """

    def __init__(
        self,
        llm: AsyncLlmClient,
        min_col_count: int = 2,
        verify_with_llm: bool = True,
        sample_row_count: int = 4,
        keyword_ratio_floor: float | None = None,
        tracker: LlmUsageTracker | None = None,
    ) -> None:
        self._llm = llm
        self._min_cols = min_col_count
        self._verify = verify_with_llm
        self._sample_rows = sample_row_count
        self._tracker = tracker
        if keyword_ratio_floor is not None:
            logger.debug("keyword_ratio_floor=%s is deprecated and ignored", keyword_ratio_floor)

    async def locate(self, doc_id: str, html_path: Path) -> list[TableRef]:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
        tables = soup.find_all("table")
        section_ctx = self._section_context_map(soup)
        req_zones = self._build_requirement_zones(soup)
        candidates = self._extract_candidates(soup, section_ctx, req_zones)
        logger.info(
            "table candidates: %d zones=%d (doc=%s)",
            len(candidates),
            len(req_zones),
            doc_id,
        )

        accepted_indices: set[int] = set()
        meta_map: dict[int, _LocatedMeta] = {}
        verdict_map: dict[int, _LlmVerdict] = {}

        def _header_qualifies(header: list[str]) -> bool:
            return header_is_requirement_table(header) or header_looks_like_requirements(header)

        def accept(idx: int, *, via: str, confidence: float, continuation: bool = False) -> None:
            cand = next((c for c in candidates if c.index == idx), None)
            if cand is None or is_administrative_table_header(cand.header):
                return
            if continuation:
                tbl = tables[idx] if idx < len(tables) else None
                if not isinstance(tbl, Tag) or not table_looks_like_requirement_continuation(tbl):
                    return
            elif not _header_qualifies(cand.header):
                return
            if idx not in accepted_indices:
                meta_map[idx] = _LocatedMeta(located_via=via, confidence=confidence)
            accepted_indices.add(idx)

        def accept_with_group(idx: int, *, via: str, confidence: float) -> None:
            accept(idx, via=via, confidence=confidence)
            for j in self._expand_requirement_group(tables, idx):
                if j != idx:
                    accept(
                        j,
                        via=via,
                        confidence=max(confidence - 0.05, 0.5),
                        continuation=True,
                    )

        # 1) 명시적 조견표 헤더 (요건 구분 + 상세내용)
        for cand in candidates:
            if header_looks_like_requirements(cand.header):
                accept_with_group(
                    cand.index,
                    via="heuristic",
                    confidence=max(cand.keyword_ratio, 0.9),
                )

        # 2) 상세 요구사항 구역 — 헤더가 조견표일 때만 (S/W 역할·HW 스펙 표 제외)
        for cand in candidates:
            if cand.index in accepted_indices:
                continue
            if not cand.in_requirement_zone:
                continue
            if header_is_requirement_table(cand.header):
                accept_with_group(
                    cand.index,
                    via="section_zone",
                    confidence=0.88,
                )

        # 3) 구역 밖이라도 헤더가 명확한 조견표
        for cand in candidates:
            if cand.index in accepted_indices:
                continue
            if is_administrative_table_header(cand.header):
                continue
            if header_is_requirement_table(cand.header):
                accept_with_group(
                    cand.index,
                    via="heuristic_body",
                    confidence=max(cand.keyword_ratio, 0.75),
                )

        # 4) LLM batch — 헤더에 요구·요청·상세 키워드가 있는 후보만
        llm_candidates = [
            c
            for c in candidates
            if c.index not in accepted_indices
            and c.col_count >= self._min_cols
            and not is_administrative_table_header(c.header)
            and _header_qualifies(c.header)
        ]
        if self._verify and llm_candidates:
            verdict_map = await self._verify_batch_with_llm(llm_candidates)
            for cand in llm_candidates:
                verdict = verdict_map.get(cand.index)
                if verdict and verdict.is_requirements_table:
                    accept_with_group(
                        cand.index,
                        via="llm",
                        confidence=verdict.confidence,
                    )

        refs: list[TableRef] = []
        cand_by_idx = {c.index: c for c in candidates}
        for idx in sorted(accepted_indices):
            cand = cand_by_idx.get(idx)
            if cand is None:
                continue
            cat_col, det_col = detect_column_indices(cand.header)
            has_cat_col = header_looks_like_requirements(cand.header)
            meta = meta_map.get(idx)
            if meta is None:
                verdict = verdict_map.get(idx)
                confidence = verdict.confidence if verdict else cand.keyword_ratio
                located_via = "llm" if idx in verdict_map else "heuristic"
            else:
                confidence = meta.confidence
                located_via = meta.located_via
            refs.append(
                TableRef(
                    doc_id=doc_id,
                    table_index=idx,
                    header_columns=cand.header,
                    confidence=confidence,
                    located_via=located_via,
                    category_col_index=cat_col,
                    detail_col_index=det_col,
                    has_category_column=has_cat_col,
                    source_page=cand.source_page,
                    section_heading=cand.section_heading,
                )
            )
        logger.info(
            "located requirement tables: %d indices %s (doc=%s)",
            len(refs),
            [r.table_index for r in refs],
            doc_id,
        )
        return refs

    @staticmethod
    def _expand_requirement_group(tables: list[Tag], start_index: int) -> list[int]:
        """조견표 시작 표 + 페이지 분할로 이어지는 표 인덱스 묶음."""
        indices = [start_index]
        for i in range(start_index + 1, len(tables)):
            tbl = tables[i]
            if not isinstance(tbl, Tag):
                break
            first = tbl.find("tr")
            if not isinstance(first, Tag):
                break
            header = [c.get_text(strip=True) for c in first.find_all(["td", "th"])]
            if _header_triggers_table_stop(header):
                break
            if table_looks_like_requirement_continuation(tbl):
                indices.append(i)
            else:
                break
        return indices

    @staticmethod
    def _page_for_table(tbl: Tag) -> int | None:
        section = tbl.find_parent("section")
        if section and section.get("data-page"):
            try:
                return int(str(section["data-page"]))
            except ValueError:
                return None
        return None

    @staticmethod
    def _section_context_map(soup: BeautifulSoup) -> dict[int, dict[str, object]]:
        """각 table index → {page, heading} — 역추적·LLM 컨텍스트용."""
        ctx: dict[int, dict[str, object]] = {}
        current_page: int | None = None
        current_heading: str | None = None
        table_idx = 0
        for node in soup.find_all(["section", "p", "h1", "h2", "h3", "h4", "table"]):
            if not isinstance(node, Tag):
                continue
            if node.name == "section" and node.get("data-page"):
                try:
                    current_page = int(str(node["data-page"]))
                except ValueError:
                    pass
                continue
            if node.name in ("p", "h1", "h2", "h3", "h4"):
                text = " ".join(node.get_text(strip=True).split())
                if not text or node.find_parent("table"):
                    continue
                m = MAJOR_SECTION_RE.match(text)
                if m:
                    current_heading = _format_section_heading(m)
                continue
            if node.name == "table":
                ctx[table_idx] = {"page": current_page, "heading": current_heading}
                table_idx += 1
        return ctx

    @staticmethod
    def _is_requirement_section_heading(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        if any(p.search(compact) or p.search(text) for p in NON_REQUIREMENT_SECTION_KEYWORDS):
            return False
        return any(p.search(compact) or p.search(text) for p in REQUIREMENT_SECTION_KEYWORDS)

    @staticmethod
    def _is_zone_closing_heading(text: str) -> bool:
        """요구사항 구역을 닫는 대목차(제출·서식·재무 등)."""
        if not MAJOR_SECTION_RE.match(text):
            return False
        compact = re.sub(r"\s+", "", text)
        if TableLocator._is_requirement_section_heading(text):
            return False
        return any(p.search(compact) or p.search(text) for p in NON_REQUIREMENT_SECTION_KEYWORDS)

    @staticmethod
    def _is_detail_requirement_content_anchor(text: str) -> bool:
        """「(2) 상세 요구사항 내용」 — 개요(1.4.3)가 아닌 본문 조견표 시작."""
        compact = re.sub(r"\s+", "", text)
        if DETAIL_REQUIREMENT_CONTENT_TITLE.search(compact):
            return True
        match = DETAIL_REQUIREMENT_CONTENT_ANCHOR.search(text)
        if match is None:
            return False
        try:
            return int(match.group("num")) >= 2
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _detail_requirement_anchor_table_index(soup: BeautifulSoup) -> int | None:
        """문서에 상세 요구사항 본문 앵커가 있으면, 그 직후 첫 표 인덱스."""
        anchor_seen = False
        table_idx = 0
        for node in soup.find_all(["section", "p", "h1", "h2", "h3", "h4", "table"]):
            if not isinstance(node, Tag):
                continue
            if node.name in ("p", "h1", "h2", "h3", "h4"):
                text = " ".join(node.get_text(strip=True).split())
                if not text or node.find_parent("table"):
                    continue
                if TableLocator._is_detail_requirement_content_anchor(text):
                    anchor_seen = True
                continue
            if node.name == "table":
                if anchor_seen:
                    return table_idx
                table_idx += 1
        return None

    @staticmethod
    def _build_requirement_zones(soup: BeautifulSoup) -> list[tuple[int, int]]:
        """(start_table_idx, end_table_idx_exclusive) — 상세 요구사항 본문 구역."""
        detail_anchor_idx = TableLocator._detail_requirement_anchor_table_index(soup)
        zones: list[tuple[int, int]] = []
        zone_start: int | None = None
        table_idx = 0
        for node in soup.find_all(["section", "p", "h1", "h2", "h3", "h4", "table"]):
            if not isinstance(node, Tag):
                continue
            if node.name in ("p", "h1", "h2", "h3", "h4"):
                text = " ".join(node.get_text(strip=True).split())
                if not text or node.find_parent("table"):
                    continue
                if TableLocator._is_detail_requirement_content_anchor(text):
                    if zone_start is not None:
                        zones.append((zone_start, table_idx))
                    zone_start = table_idx
                    continue
                m = MAJOR_SECTION_RE.match(text)
                if not m:
                    continue
                if TableLocator._is_requirement_section_heading(text):
                    if detail_anchor_idx is not None:
                        continue
                    if zone_start is None:
                        zone_start = table_idx
                elif TableLocator._is_zone_closing_heading(text) and zone_start is not None:
                    zones.append((zone_start, table_idx))
                    zone_start = None
                continue
            if node.name == "table":
                table_idx += 1
        if zone_start is not None:
            zones.append((zone_start, table_idx))
        return zones

    @staticmethod
    def _table_in_zones(table_index: int, zones: list[tuple[int, int]]) -> bool:
        return any(start <= table_index < end for start, end in zones)

    def _extract_candidates(
        self,
        soup: BeautifulSoup,
        section_ctx: dict[int, dict[str, object]],
        req_zones: list[tuple[int, int]],
    ) -> list[_Candidate]:
        out: list[_Candidate] = []
        for idx, tbl in enumerate(soup.find_all("table")):
            if not isinstance(tbl, Tag):
                continue
            first_row = tbl.find("tr")
            if not isinstance(first_row, Tag):
                continue
            header_cells = [c.get_text(strip=True) for c in first_row.find_all(["td", "th"])]
            if not header_cells:
                continue
            rows = tbl.find_all("tr")
            hits = sum(1 for h in header_cells if any(kw in h for kw in HEADER_KEYWORDS))
            ratio = hits / len(header_cells) if header_cells else 0.0
            ctx = section_ctx.get(idx, {})
            out.append(
                _Candidate(
                    index=idx,
                    header=header_cells,
                    row_count=len(rows),
                    col_count=len(header_cells),
                    keyword_hits=hits,
                    keyword_ratio=ratio,
                    sample_rows=self._sample_rows_from_table(tbl, header_cells),
                    section_heading=str(ctx["heading"]) if ctx.get("heading") else None,
                    source_page=int(ctx["page"]) if ctx.get("page") is not None else None,
                    in_requirement_zone=self._table_in_zones(idx, req_zones),
                )
            )
        return out

    def _sample_rows_from_table(self, tbl: Tag, header: list[str]) -> list[list[str]]:
        rows = tbl.find_all("tr")
        start = 1 if is_short_header_row(header) or header_looks_like_requirements(header) else 0
        samples: list[list[str]] = []
        for tr in rows[start:]:
            if len(samples) >= self._sample_rows:
                break
            if not isinstance(tr, Tag):
                continue
            cells = [c.get_text("\n", strip=True)[:240] for c in tr.find_all(["td", "th"])]
            if cells and any(len(c) > 12 for c in cells):
                samples.append(cells)
        return samples

    async def _verify_batch_with_llm(
        self, candidates: list[_Candidate]
    ) -> dict[int, _LlmVerdict]:
        """후보 표들을 LLM 1회 호출로 일괄 검증 — 헤더·샘플 본문 포함."""
        if len(candidates) == 1:
            v = await self._verify_with_llm(candidates[0])
            return {candidates[0].index: v}

        blocks = [self._format_candidate(c) for c in candidates]
        prompt = (
            "다음은 RFP 문서 내 후보 `<table>` 목록이다. "
            "각 표가 '요구사항(조견표) 표'인지 판정하라.\n\n"
            "**헤더에 요구·요건·요청·상세내용·구축범위 등이 없으면 반드시 false.**\n"
            "조견표: 업체가 제안·구현해야 할 기능·기술·데이터 요구가 나열된 표.\n"
            "제외: 입찰서식, 제출서류, H/W 스펙(CPU/GPU/MEM), S/W 역할·제안기준, 일정, 견적, 재무.\n"
            "헤더 키워드 없이 본문만으로 true 금지.\n"
            "「구분+요구사항 상세」「구분+구축 범위」 및 페이지 분할 연속 표 포함.\n"
            "금융·AI RFP는 조견표가 **여러 개**(과제별·기능별·영역별) — 하나만 고르지 말고 해당되는 표는 모두 true.\n\n"
            + "\n\n".join(blocks)
            + '\n\nJSON: {"results": [{"table_index": <int>, "is_requirements_table": <bool>, '
            '"confidence": <0.0~1.0>}, ...]} — 입력 index마다 1개씩.'
        )
        try:
            batch = await self._llm.structured_output(
                [Message(role="user", content=prompt)],
                _LlmBatchVerdict,
                purpose="table_locator:batch",
                tracker=self._tracker,
            )
            return {
                item.table_index: _LlmVerdict(
                    is_requirements_table=item.is_requirements_table,
                    confidence=item.confidence,
                )
                for item in batch.results
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 일괄 검증 실패, 개별 검증으로 폴백: %s", e)
            verdicts = await asyncio.gather(*[self._verify_with_llm(c) for c in candidates])
            return {c.index: v for c, v in zip(candidates, verdicts, strict=True)}

    @staticmethod
    def _format_candidate(c: _Candidate) -> str:
        lines = [
            f"- index={c.index}, header={c.header}, rows={c.row_count}, cols={c.col_count}, "
            f"keyword_ratio={c.keyword_ratio:.2f}, section={c.section_heading!r}, page={c.source_page}"
        ]
        if c.sample_rows:
            lines.append("  sample_rows:")
            for i, row in enumerate(c.sample_rows, 1):
                joined = " | ".join(row[:8])
                lines.append(f"    {i}: {joined}")
        return "\n".join(lines)

    async def _verify_with_llm(self, cand: _Candidate) -> _LlmVerdict:
        block = self._format_candidate(cand)
        prompt = (
            "다음 RFP 후보 표가 '요구사항(조견표) 표'인지 판정하라. "
            "헤더에 요구·요건·상세 키워드 없으면 false. H/W 스펙·S/W 역할 표는 false.\n\n"
            f"{block}\n\n"
            'JSON: {"is_requirements_table": <bool>, "confidence": <0.0~1.0>}'
        )
        try:
            return await self._llm.structured_output(
                [Message(role="user", content=prompt)],
                _LlmVerdict,
                purpose="table_locator:single",
                tracker=self._tracker,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 검증 실패: %s", e)
            return _LlmVerdict(is_requirements_table=False, confidence=0.0)
