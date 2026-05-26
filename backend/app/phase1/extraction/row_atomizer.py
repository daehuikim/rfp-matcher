from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel

from app.domain.models import AtomicRow, TableRef
from app.llm.base import AsyncLlmClient, Message

from .parsing import split_by_markers
from .table_columns import (
    detect_column_indices,
    extract_category_and_detail,
    first_row_is_content,
    is_short_header_row,
    row_has_requirement_body,
)

logger = logging.getLogger(__name__)


class _LlmAtoms(BaseModel):
    atoms: list[str]


class RowAtomizer:
    """
    조견표 한 행(또는 셀)을 atomic 단위로 분해.

    절차:
      1) 헤더에서 '요건 구분'·'상세내용' 열 위치 파악 (PyMuPDF 분할 표 대응)
      2) 각 행의 상세내용 셀 추출 (마지막 열 고정 가정 제거)
      3) 룰(`split_by_markers`)로 ①②③·볼렛 단위 분해
      4) 필요 시 LLM 보완
    """

    def __init__(self, llm: AsyncLlmClient, llm_fallback: bool = True, llm_concurrency: int = 8) -> None:
        self._llm = llm
        self._llm_fallback = llm_fallback
        self._sem = asyncio.Semaphore(llm_concurrency)

    async def atomize(
        self,
        doc_id: str,
        html_path: Path,
        table_ref: TableRef,
    ) -> list[AtomicRow]:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
        tables = soup.find_all("table")
        if table_ref.table_index >= len(tables):
            logger.warning("table_index 범위 초과: %d / %d", table_ref.table_index, len(tables))
            return []
        tbl = tables[table_ref.table_index]
        if not isinstance(tbl, Tag):
            return []
        rows = tbl.find_all("tr")
        if not rows:
            return []

        first_cells = rows[0].find_all(["td", "th"]) if isinstance(rows[0], Tag) else []
        header_texts = [c.get_text(strip=True) for c in first_cells]

        if table_ref.category_col_index is not None and table_ref.detail_col_index is not None:
            category_col, detail_col = table_ref.category_col_index, table_ref.detail_col_index
        elif header_texts and not first_row_is_content(header_texts):
            category_col, detail_col = detect_column_indices(header_texts)
        else:
            # 페이지 분할 조각: 보통 col1=분류, col2=상세
            category_col, detail_col = 1, 2

        skip_first = bool(header_texts) and is_short_header_row(header_texts)
        start = 1 if skip_first else 0

        out: list[AtomicRow] = []
        seen_detail: set[str] = set()
        row_tasks: list[asyncio.Task[list[AtomicRow]]] = []

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
            if len(detail.strip()) < 10:
                continue
            norm = detail.strip()
            if norm in seen_detail:
                continue
            seen_detail.add(norm)
            row_tasks.append(
                asyncio.create_task(
                    self._atomize_row(
                        doc_id=doc_id,
                        table_index=table_ref.table_index,
                        category_raw=category_raw,
                        detail=detail,
                    )
                )
            )

        if row_tasks:
            for part in await asyncio.gather(*row_tasks):
                out.extend(part)
        return out

    async def _atomize_row(
        self,
        *,
        doc_id: str,
        table_index: int,
        category_raw: str | None,
        detail: str,
    ) -> list[AtomicRow]:
        atoms = await self._split(detail)
        return [
            AtomicRow(
                doc_id=doc_id,
                table_index=table_index,
                source_cell=detail,
                bullet_marker=atom.marker,
                text=atom.text,
                row_seq=seq,
                category_raw=category_raw,
            )
            for seq, atom in enumerate(atoms)
        ]

    async def _split(self, cell: str) -> list[_AtomLike]:
        rule_based = split_by_markers(cell)
        if (
            self._llm_fallback
            and rule_based
            and all(a.marker is None for a in rule_based)
            and _has_likely_multi_item(cell)
        ):
            llm_result = await self._llm_split(cell)
            if llm_result is not None:
                return [_AtomLike(marker=None, text=t) for t in llm_result]
        return [_AtomLike(marker=a.marker, text=a.text) for a in rule_based]

    async def _llm_split(self, cell: str) -> list[str] | None:
        prompt = (
            "다음은 RFP 조견표 한 셀의 본문이다. 명시적 ①②③ 같은 마커는 없지만, "
            "서로 다른 요구사항이 한 셀에 섞여 있을 수 있다. "
            "의미 단위로 잘게 쪼개서 JSON으로만 반환하라.\n"
            f"본문:\n{cell}\n"
            '응답 형식: {"atoms": ["...", "..."]}'
        )
        try:
            async with self._sem:
                result = await self._llm.structured_output(
                    [Message(role="user", content=prompt)],
                    _LlmAtoms,
                )
            return [a for a in result.atoms if a.strip()]
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 분해 실패, 룰 결과 유지: %s", e)
            return None


class _AtomLike:
    __slots__ = ("marker", "text")

    def __init__(self, marker: str | None, text: str) -> None:
        self.marker = marker
        self.text = text


def _has_likely_multi_item(text: str) -> bool:
    return text.count("\n") >= 2 or text.count(".") >= 3


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
