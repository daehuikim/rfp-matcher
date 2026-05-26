from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from app.domain.models import SectionRef
from app.phase1.extraction.fallback.section_patterns import (
    BLOCK_TAGS,
    MAJOR_SECTION_RE,
    MIN_SECTION_BLOCK_SPAN,
    REQUIREMENT_SECTION_KEYWORDS,
    format_section_heading,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Block:
    index: int
    text: str


@dataclass(frozen=True)
class _MajorHeading:
    block_index: int
    heading: str
    level: int


class SectionLocator:
    """
    HTML 본문에서 요구사항 대목차(예: 3. 프로젝트 범위)를 찾는다.

    조견표 `<table>` 이 없거나 비어 있을 때 AtomizationCoordinator 폴백에 사용.
    """

    def locate(self, doc_id: str, html_path: Path) -> list[SectionRef]:
        blocks = self._load_blocks(html_path)
        if not blocks:
            return []

        majors = self._find_major_headings(blocks)
        req_sections = [h for h in majors if self._is_requirement_section(h.heading)]
        if not req_sections:
            logger.info("section locate: requirement 대목차 없음 doc=%s", doc_id)
            return []

        refs: list[SectionRef] = []
        for i, head in enumerate(req_sections):
            end = self._section_end_block(blocks, majors, head)
            span = end - head.block_index
            if span < MIN_SECTION_BLOCK_SPAN:
                logger.debug(
                    "section skip (too short): %s span=%d",
                    head.heading[:40],
                    span,
                )
                continue
            refs.append(
                SectionRef(
                    doc_id=doc_id,
                    section_index=len(refs),
                    heading=head.heading,
                    level=head.level,
                    block_start=head.block_index,
                    block_end=end,
                    located_via="section_heuristic",
                    confidence=0.85,
                )
            )
        logger.info(
            "section locate: %d requirement sections doc=%s headings=%s",
            len(refs),
            doc_id,
            [r.heading[:32] for r in refs],
        )
        return refs

    @staticmethod
    def _load_blocks(html_path: Path) -> list[_Block]:
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
        body = soup.body or soup
        blocks: list[_Block] = []
        idx = 0
        for node in body.find_all(BLOCK_TAGS):
            if not isinstance(node, Tag):
                continue
            if node.find_parent("table"):
                continue
            text = " ".join(node.get_text(strip=True).split())
            if not text:
                continue
            blocks.append(_Block(index=idx, text=text))
            idx += 1
        return blocks

    @staticmethod
    def _parse_major(text: str) -> _MajorHeading | None:
        m = MAJOR_SECTION_RE.match(text)
        if not m:
            return None
        if m.group("num_title"):
            return _MajorHeading(
                block_index=-1,
                heading=format_section_heading(m),
                level=1,
            )
        if m.group("roman_title"):
            title = m.group("roman_title").strip()
            heading = f"{m.group('roman')}. {title}"
            return _MajorHeading(block_index=-1, heading=heading, level=1)
        return None

    def _find_major_headings(self, blocks: list[_Block]) -> list[_MajorHeading]:
        out: list[_MajorHeading] = []
        for block in blocks:
            parsed = self._parse_major(block.text)
            if parsed is None:
                continue
            out.append(
                _MajorHeading(
                    block_index=block.index,
                    heading=parsed.heading,
                    level=parsed.level,
                )
            )
        return out

    @staticmethod
    def _is_requirement_section(heading: str) -> bool:
        compact = re.sub(r"\s+", "", heading)
        return any(p.search(compact) or p.search(heading) for p in REQUIREMENT_SECTION_KEYWORDS)

    @staticmethod
    def _section_end_block(
        blocks: list[_Block],
        majors: list[_MajorHeading],
        current: _MajorHeading,
    ) -> int:
        for head in majors:
            if head.block_index <= current.block_index:
                continue
            if head.level <= current.level:
                return head.block_index
        return blocks[-1].index + 1 if blocks else current.block_index + 1

    @staticmethod
    def get_block_texts(html_path: Path) -> list[str]:
        return [b.text for b in SectionLocator._load_blocks(html_path)]
