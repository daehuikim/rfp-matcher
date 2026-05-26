from __future__ import annotations

import logging
import re
from pathlib import Path

from app.domain.models import AtomicRow, SectionRef
from app.phase1.extraction.fallback.section_locator import SectionLocator
from app.phase1.extraction.fallback.section_patterns import (
    MIN_ATOM_TEXT_LEN,
    NOISE_ONLY_RE,
    SUBSECTION_RE,
)
from app.phase1.extraction.parsing import normalize_line_breaks, split_by_markers

logger = logging.getLogger(__name__)


class SectionAtomizer:
    """SectionRef 구간의 블록 텍스트 → atomic 요구사항."""

    def atomize(
        self,
        doc_id: str,
        html_path: Path,
        section_ref: SectionRef,
    ) -> list[AtomicRow]:
        blocks = SectionLocator._load_blocks(html_path)
        slice_blocks = [
            b for b in blocks if section_ref.block_start <= b.index < section_ref.block_end
        ]
        if not slice_blocks:
            return []

        lines = self._blocks_to_lines([b.text for b in slice_blocks], section_ref.heading)
        lines = self._drop_preamble(lines)
        chunks = self._group_by_subsection(lines, section_ref.heading)

        atoms: list[AtomicRow] = []
        row_seq = 0
        for category_raw, chunk_lines in chunks:
            cell_text = normalize_line_breaks("\n".join(chunk_lines))
            if len(cell_text.strip()) < MIN_ATOM_TEXT_LEN:
                continue
            for atom in split_by_markers(cell_text):
                text = normalize_line_breaks(atom.text)
                if len(text) < MIN_ATOM_TEXT_LEN:
                    continue
                if category_raw == section_ref.heading and atom.marker is None:
                    continue
                atoms.append(
                    AtomicRow(
                        doc_id=doc_id,
                        table_index=None,
                        source_cell=cell_text,
                        bullet_marker=atom.marker,
                        text=text,
                        row_seq=row_seq,
                        category_raw=category_raw,
                        section_index=section_ref.section_index,
                    )
                )
                row_seq += 1

        logger.info(
            "section atomize: section=%d atoms=%d heading=%s",
            section_ref.section_index,
            len(atoms),
            section_ref.heading[:40],
        )
        return atoms

    @staticmethod
    def _blocks_to_lines(blocks: list[str], section_heading: str) -> list[str]:
        lines: list[str] = []
        pending_marker: str | None = None
        heading_norm = " ".join(section_heading.split())

        for raw in blocks:
            text = " ".join(raw.split())
            if not text:
                continue
            if text == heading_norm or text.endswith(heading_norm):
                continue
            if NOISE_ONLY_RE.match(text):
                if text in ("⦁", "•", "-", "○"):
                    pending_marker = text
                continue
            if pending_marker:
                text = f"{pending_marker} {text}"
                pending_marker = None

            if lines and _should_append_to_previous(lines[-1], text):
                lines[-1] = f"{lines[-1]} {text}"
            else:
                lines.append(text)
        return lines

    @staticmethod
    def _drop_preamble(lines: list[str]) -> list[str]:
        """첫 중목차(가.) 또는 번호항목(1)) 전까지 PDF 조각 제거."""
        for i, line in enumerate(lines):
            if SUBSECTION_RE.match(line):
                return lines[i:]
            if re.match(r"^\d+[.)]", line.strip()):
                return lines[i:]
        return lines

    @staticmethod
    def _group_by_subsection(
        lines: list[str],
        section_heading: str,
    ) -> list[tuple[str, list[str]]]:
        chunks: list[tuple[str, list[str]]] = []
        current_cat = section_heading
        buf: list[str] = []

        def flush() -> None:
            nonlocal buf
            if buf:
                chunks.append((current_cat, buf))
                buf = []

        for line in lines:
            sub = SUBSECTION_RE.match(line)
            if sub:
                flush()
                current_cat = line.strip()
                continue
            buf.append(line)
        flush()
        return chunks


def _should_append_to_previous(prev: str, nxt: str) -> bool:
    if SUBSECTION_RE.match(nxt):
        return False
    if re.match(r"^\s*[\d]+[.)]", nxt):
        return False
    if re.match(r"^[①-⑳]", nxt):
        return False
    if len(nxt) <= 5:
        return True
    if prev.endswith((":", "：", "위한", "및", "또는")):
        return True
    last_word = prev.split()[-1] if prev.split() else prev
    if len(last_word) <= 4 and re.search(r"[가-힣]$", last_word):
        return True
    return False
