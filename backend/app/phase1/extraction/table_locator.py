from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel

from app.domain.models import TableRef
from app.llm.base import AsyncLlmClient, Message

from .table_columns import (
    STOP_TABLE_KEYWORDS,
    detect_column_indices,
    header_looks_like_requirements,
    table_looks_like_requirement_continuation,
)

logger = logging.getLogger(__name__)

# 조견표 헤더에 자주 등장하는 한국어 키워드
HEADER_KEYWORDS = {
    "요건",
    "요건구분",
    "요건 구분",
    "구분",
    "상세내용",
    "상세 내용",
    "내용",
    "요구사항",
    "분류",
    "명칭",
    "설명",
    "정의",
}


@dataclass
class _Candidate:
    index: int  # html 내 table 태그 순번
    header: list[str]
    row_count: int
    col_count: int
    keyword_hits: int = 0
    keyword_ratio: float = 0.0


class _LlmVerdict(BaseModel):
    is_requirements_table: bool
    confidence: float


class _BatchItem(BaseModel):
    table_index: int
    is_requirements_table: bool
    confidence: float


class _LlmBatchVerdict(BaseModel):
    results: list[_BatchItem]


class TableLocator:
    """
    HTML 문서에서 조견표 위치를 탐지한다.

    1) '요건 구분'+'상세내용' 헤더가 있는 표를 우선 탐지
    2) PyMuPDF 페이지 분할로 잘린 연속 표를 같은 조견표 그룹으로 확장
    3) 휴리스틱·LLM으로 모호한 후보 보완
    """

    def __init__(
        self,
        llm: AsyncLlmClient,
        keyword_ratio_floor: float = 0.5,
        min_col_count: int = 2,
        verify_with_llm: bool = True,
    ) -> None:
        self._llm = llm
        self._floor = keyword_ratio_floor
        self._min_cols = min_col_count
        self._verify = verify_with_llm

    async def locate(self, doc_id: str, html_path: Path) -> list[TableRef]:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
        tables = soup.find_all("table")
        candidates = self._extract_candidates(soup)
        logger.info("table candidates: %d (doc=%s)", len(candidates), doc_id)

        accepted_indices: set[int] = set()

        # 1) 명시적 조견표 헤더 (요건 구분 + 상세내용)
        for cand in candidates:
            if header_looks_like_requirements(cand.header):
                accepted_indices.update(self._expand_requirement_group(tables, cand.index))

        # 2) 기존 휴리스틱·LLM (헤더 키워드 비율)
        llm_candidates: list[_Candidate] = []
        verdict_map: dict[int, _LlmVerdict] = {}
        for cand in candidates:
            if cand.index in accepted_indices:
                continue
            if cand.col_count < self._min_cols:
                continue
            if cand.keyword_ratio >= self._floor:
                accepted_indices.add(cand.index)
                continue
            if self._verify and 0.1 <= cand.keyword_ratio < self._floor:
                llm_candidates.append(cand)

        if llm_candidates:
            verdict_map = await self._verify_batch_with_llm(llm_candidates)
            for cand in llm_candidates:
                verdict = verdict_map.get(cand.index)
                if verdict and verdict.is_requirements_table:
                    if header_looks_like_requirements(cand.header):
                        accepted_indices.update(self._expand_requirement_group(tables, cand.index))
                    else:
                        accepted_indices.add(cand.index)

        refs: list[TableRef] = []
        cand_by_idx = {c.index: c for c in candidates}
        for idx in sorted(accepted_indices):
            cand = cand_by_idx.get(idx)
            if cand is None:
                continue
            cat_col, det_col = detect_column_indices(cand.header)
            is_heuristic = header_looks_like_requirements(cand.header)
            if is_heuristic:
                confidence = max(cand.keyword_ratio, 0.9)
                located_via = "heuristic"
            else:
                verdict = verdict_map.get(cand.index)
                confidence = verdict.confidence if verdict else cand.keyword_ratio
                located_via = "llm"
            refs.append(
                TableRef(
                    doc_id=doc_id,
                    table_index=idx,
                    header_columns=cand.header,
                    confidence=confidence,
                    located_via=located_via,
                    category_col_index=cat_col,
                    detail_col_index=det_col,
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
            if any(kw in " ".join(header) for kw in STOP_TABLE_KEYWORDS):
                break
            if table_looks_like_requirement_continuation(tbl):
                indices.append(i)
            else:
                break
        return indices

    def _extract_candidates(self, soup: BeautifulSoup) -> list[_Candidate]:
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
            out.append(
                _Candidate(
                    index=idx,
                    header=header_cells,
                    row_count=len(rows),
                    col_count=len(header_cells),
                    keyword_hits=hits,
                    keyword_ratio=ratio,
                )
            )
        return out

    async def _verify_batch_with_llm(
        self, candidates: list[_Candidate]
    ) -> dict[int, _LlmVerdict]:
        """모호한 후보 표들을 LLM 1회 호출로 일괄 검증 — N회 순차/병렬 API 대비 지연 감소."""
        if len(candidates) == 1:
            v = await self._verify_with_llm(candidates[0])
            return {candidates[0].index: v}

        lines = []
        for c in candidates:
            lines.append(
                f"- index={c.index}, header={c.header}, rows={c.row_count}, cols={c.col_count}, "
                f"keyword_ratio={c.keyword_ratio:.2f}"
            )
        prompt = (
            "다음은 RFP 문서 내 후보 표 목록이다. 각 표가 '요구사항(조견표) 표'인지 판정하라.\n\n"
            + "\n".join(lines)
            + '\n\nJSON: {"results": [{"table_index": <int>, "is_requirements_table": <bool>, '
            '"confidence": <0.0~1.0>}, ...]} — 입력 index마다 1개씩.'
        )
        try:
            batch = await self._llm.structured_output(
                [Message(role="user", content=prompt)],
                _LlmBatchVerdict,
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

    async def _verify_with_llm(self, cand: _Candidate) -> _LlmVerdict:
        prompt = (
            "다음은 RFP 문서 내 한 표의 헤더 행이다. 이 표가 '요구사항(조견표) 표'인지 판정하라.\n"
            f"헤더 컬럼: {cand.header}\n"
            f"총 행 수: {cand.row_count}, 열 수: {cand.col_count}\n"
            'JSON으로만 응답: {"is_requirements_table": <bool>, "confidence": <0.0~1.0>}'
        )
        try:
            return await self._llm.structured_output(
                [Message(role="user", content=prompt)],
                _LlmVerdict,
            )
        except Exception as e:  # noqa: BLE001 — 검증 실패는 폴백 사유
            logger.warning("LLM 검증 실패, 휴리스틱 결과 유지: %s", e)
            return _LlmVerdict(is_requirements_table=False, confidence=0.0)
