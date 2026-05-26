from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.models import AtomicRow, SectionRef, TableRef
from app.llm.base import AsyncLlmClient
from app.phase1.extraction.fallback.section_atomizer import SectionAtomizer
from app.phase1.extraction.fallback.section_locator import SectionLocator
from app.phase1.extraction.row_atomizer import ParagraphAtomizer, RowAtomizer

if TYPE_CHECKING:
    from app.llm.usage import LlmUsageTracker

logger = logging.getLogger(__name__)


class AtomizationStrategy(StrEnum):
    TABLE = "table"
    SECTION = "section"
    PARAGRAPH = "paragraph"


@dataclass(frozen=True)
class AtomizationResult:
    atoms: list[AtomicRow]
    strategy: AtomizationStrategy
    tables_used: int = 0
    sections_used: int = 0


class AtomizationCoordinator:
    """
    조견표 → 섹션 → 단락 순 폴백으로 atomic 분해.

    1) TableRef + RowAtomizer (조견표) — **탐지됐으면 표 본문만** (섹션 보충 없음)
    2) SectionRef + SectionAtomizer (표 없을 때)
    3) ParagraphAtomizer (최후)
    """

    def __init__(
        self,
        llm: AsyncLlmClient,
        *,
        tracker: LlmUsageTracker | None = None,
    ) -> None:
        self._row_atomizer = RowAtomizer(llm, tracker=tracker)
        self._section_locator = SectionLocator()
        self._section_atomizer = SectionAtomizer()
        self._paragraph_atomizer = ParagraphAtomizer()

    async def atomize(
        self,
        doc_id: str,
        html_path: Path,
        table_refs: list[TableRef],
    ) -> AtomizationResult:
        if table_refs:
            table_atoms = await self._atomize_tables(doc_id, html_path, table_refs)
            if table_atoms:
                logger.info(
                    "atomize: strategy=table atoms=%d tables=%d",
                    len(table_atoms),
                    len(table_refs),
                )
                return AtomizationResult(
                    atoms=table_atoms,
                    strategy=AtomizationStrategy.TABLE,
                    tables_used=len(table_refs),
                )
            logger.info(
                "atomize: table refs=%d but atoms=0 — section 폴백",
                len(table_refs),
            )

        section_refs = self._section_locator.locate(doc_id, html_path)
        if section_refs:
            section_atoms = self._atomize_sections(doc_id, html_path, section_refs)
            if section_atoms:
                logger.info(
                    "atomize: strategy=section atoms=%d sections=%d",
                    len(section_atoms),
                    len(section_refs),
                )
                return AtomizationResult(
                    atoms=section_atoms,
                    strategy=AtomizationStrategy.SECTION,
                    sections_used=len(section_refs),
                )

        logger.info("atomize: strategy=paragraph (last resort) doc=%s", doc_id)
        paragraph_atoms = await self._paragraph_atomizer.atomize(doc_id, html_path)
        return AtomizationResult(
            atoms=paragraph_atoms,
            strategy=AtomizationStrategy.PARAGRAPH,
        )

    async def _atomize_tables(
        self,
        doc_id: str,
        html_path: Path,
        refs: list[TableRef],
    ) -> list[AtomicRow]:
        out: list[AtomicRow] = []
        carry_category: str | None = None
        for ref in refs:
            part, carry_category = await self._row_atomizer.atomize(
                doc_id,
                html_path,
                ref,
                carry_category=carry_category,
            )
            out.extend(part)
        return out

    def _atomize_sections(
        self,
        doc_id: str,
        html_path: Path,
        refs: list[SectionRef],
    ) -> list[AtomicRow]:
        out: list[AtomicRow] = []
        for ref in refs:
            out.extend(self._section_atomizer.atomize(doc_id, html_path, ref))
        return out

    def _try_section_supplement(
        self,
        doc_id: str,
        html_path: Path,
        existing: list[AtomicRow],
    ) -> list[AtomicRow]:
        section_refs = self._section_locator.locate(doc_id, html_path)
        if not section_refs:
            return []
        section_atoms = self._atomize_sections(doc_id, html_path, section_refs)
        return self._merge_atoms(existing, section_atoms)

    @staticmethod
    def _merge_atoms(primary: list[AtomicRow], secondary: list[AtomicRow]) -> list[AtomicRow]:
        seen = {a.text.strip() for a in primary if a.text.strip()}
        out = list(primary)
        for atom in secondary:
            key = atom.text.strip()
            if len(key) < 12 or key in seen:
                continue
            seen.add(key)
            out.append(atom)
        return out
