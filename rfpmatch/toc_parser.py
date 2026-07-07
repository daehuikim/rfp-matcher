from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup, Tag

from rfpmatch.shared_models import Section, TocItem


TOC_HEADING_PATTERN = re.compile(r"^(목차|contents|table\s+of\s+contents)$", re.IGNORECASE)
TOC_HEADING_COMPACT_PATTERN = re.compile(r"^(목차|목\s*차|contents|table\s*of\s*contents)$", re.IGNORECASE)
TOC_LINE_PATTERN = re.compile(
    r"^\s*(?P<number>(?:\d+[\.\-])*\d+[\.\-]?)?\s*(?P<title>.+?)\s*(?:[.\-_·•\s]{2,}(?P<page>\d{1,4}))?\s*$"
)
TRAILING_PAGE_PATTERN = re.compile(r"[.\-_·•\s]{2,}(?P<page>\d{1,4})\s*$")
LEADING_NUMBER_PATTERN = re.compile(r"^(?:\d+[\.\-])*\d+[\.\-]?\s+")

_ANCHOR_NUMERAL_MAP = str.maketrans(
    {
        "Ⅰ": "I",
        "Ⅱ": "II",
        "Ⅲ": "III",
        "Ⅳ": "IV",
        "Ⅴ": "V",
        "Ⅵ": "VI",
        "Ⅶ": "VII",
        "Ⅷ": "VIII",
        "Ⅸ": "IX",
        "Ⅹ": "X",
        "Ⅺ": "XI",
        "Ⅻ": "XII",
        "ⅰ": "i",
        "ⅱ": "ii",
        "ⅲ": "iii",
        "ⅳ": "iv",
        "ⅴ": "v",
        "ⅵ": "vi",
        "ⅶ": "vii",
        "ⅷ": "viii",
        "ⅸ": "ix",
        "ⅹ": "x",
        "ⅺ": "xi",
        "ⅻ": "xii",
    }
)


def _normalize_anchor_numerals(value: str) -> str:
    return (value or "").translate(_ANCHOR_NUMERAL_MAP)


def _slugify(value: str) -> str:
    compact = re.sub(r"\s+", " ", (value or "")).strip()
    if not compact:
        return "section"
    has_bullet_prefix = bool(re.match(r"^[\-\*•·▪■◆▶◦○□◇※]+\s*", compact))
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇※]+\s*", "", compact)
    compact = _normalize_anchor_numerals(compact)
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", compact).strip("-").lower()
    if has_bullet_prefix:
        cleaned = f"bullet-{cleaned}" if cleaned else "bullet"
    return cleaned or "section"


def _anchor_from_text(value: str, *, has_bullet_prefix: bool = False) -> str:
    anchor = _slugify(value)
    if has_bullet_prefix and not anchor.startswith("bullet-") and anchor != "bullet":
        anchor = f"bullet-{anchor}" if anchor else "bullet"
    return anchor


def _normalize_title(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip().lower()
    value = re.sub(r"^[\d.\-]+\s*", "", value)
    return value


def _parse_page(value: str | None) -> int | None:
    if not value:
        return None
    try:
        page = int(value)
    except ValueError:
        return None
    return page if page > 0 else None


def _clone_toc_item(
    item: TocItem,
    *,
    page_idx: int | None | object = ...,
    page_estimate: int | None | object = ...,
) -> TocItem:
    return TocItem(
        level=item.level,
        title=item.title,
        anchor=item.anchor,
        page_idx=item.page_idx if page_idx is ... else page_idx,
        page_estimate=item.page_estimate if page_estimate is ... else page_estimate,
    )


_FOLLOWING_PAGE_MARKER_PATTERNS = (
    re.compile(r"^\s*\[?\s*p\.?\s*(?P<page>\d{1,4})\s*\]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*p\.?\s*(?P<page>\d{1,4})\s*$", re.IGNORECASE),
    re.compile(r"^\s*page\s*[:.]?\s*(?P<page>\d{1,4})\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?P<page>\d{1,4})\s*쪽\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?P<page>\d{1,4})\s*페이지\s*$", re.IGNORECASE),
    re.compile(r"^\s*\[?p\.(?P<page>\d{1,4})\]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\[?\s*(?P<page>\d{1,4})\s*[-–—/]\s*(?:\d{1,4})\s*\]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*[-–—]\s*(?P<page>\d{1,4})\s*[-–—]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?P<page>\d{1,4})\s*[-–—]\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?P<page>\d{1,4})\s*/\s*\d{1,4}\s*$", re.IGNORECASE),
)


def _extract_page_marker_from_text(text: str) -> int | None:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return None
    for pattern in _FOLLOWING_PAGE_MARKER_PATTERNS:
        match = pattern.search(compact)
        if match:
            return _parse_page(match.group("page"))
    return None


def _find_next_page_marker_idx(lines: list[str], start_idx: int, lookahead: int = 60) -> int | None:
    if start_idx < 0 or start_idx >= len(lines):
        return None
    # 제목 뒤에 처음 나오는 페이지 표식을 우선 사용한다.
    # 거리가 멀어도 뒤쪽 전체를 훑어 첫 번째 표식을 반환한다.
    end_idx = len(lines)
    for idx in range(start_idx + 1, end_idx):
        marker_idx = _extract_page_marker_from_text(lines[idx])
        if marker_idx is not None:
            return marker_idx
    return None


def _extract_level_from_number(number: str | None, fallback: int = 1) -> int:
    if not number:
        return min(max(fallback, 1), 3)
    number = number.strip().rstrip(".-")
    if not number:
        return min(max(fallback, 1), 3)
    return min(number.count(".") + 1, 3)


def _extract_page_from_context(tag: Tag) -> int | None:
    current = tag
    for _ in range(5):
        if current is None or not isinstance(current, Tag):
            break
        for key in ("data-page", "page_idx", "page-index", "page_no", "page"):
            raw_page = current.get(key)
            if raw_page in (None, "", "None"):
                continue
            try:
                return int(raw_page)
            except (TypeError, ValueError):
                match = re.search(r"\d+", str(raw_page))
                if match:
                    try:
                        return int(match.group(0))
                    except ValueError:
                        continue
        current = current.parent
    return None


def _is_toc_heading(tag: Tag) -> bool:
    if not re.fullmatch(r"h[1-6]", tag.name or ""):
        return False
    return bool(TOC_HEADING_COMPACT_PATTERN.match(tag.get_text(" ", strip=True)))


def _is_toc_signal_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return False
    normalized = compact.lower()
    compact_no_space = re.sub(r"\s+", "", normalized)
    keyword_hits = (
        "목차",
        "목차및요령",
        "작성목차",
        "제안서작성목차",
        "제안목차",
        "contents",
        "tableofcontents",
        "table of contents",
    )
    if any(keyword in normalized or keyword in compact_no_space for keyword in keyword_hits):
        return True
    if re.search(r"[.\-_·•]{2,}\s*\d{1,4}\b", compact):
        return True
    if re.search(r"[.\-_·•]{4,}", compact):
        return True
    if "..." in compact_no_space:
        return True
    return False


def _block_has_toc_signal(block: Tag) -> bool:
    text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()
    return _is_toc_signal_text(text)


def _collect_toc_region_indices(blocks: list[Tag]) -> set[int]:
    excluded: set[int] = set()
    if not blocks:
        return excluded
    for signal_idx, block in enumerate(blocks):
        if not _block_has_toc_signal(block):
            continue
        region_end = len(blocks)
        for idx in range(signal_idx + 1, len(blocks)):
            if _looks_like_body_start_block(blocks[idx]):
                region_end = idx
                break
        excluded.update(range(signal_idx, region_end))
    return excluded


def _looks_like_body_start_block(tag: Tag) -> bool:
    if not isinstance(tag, Tag):
        return False
    if not re.fullmatch(r"h[1-6]", tag.name or "") and tag.name not in {"p", "div", "section", "figcaption"}:
        return False
    text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
    if not text:
        return False
    if re.match(r"^[\-\*•·▪■▶]\s*", text):
        return False
    # Remove trailing leader/page noise and inspect the remaining heading text.
    text = re.sub(r"[.\-_·•\s]{2,}\d{1,4}\s*$", "", text).strip()
    if not text:
        return False
    if re.search(r"\d{1,4}\s*$", text) and re.search(r"[.\-_·•\s]{2,}", tag.get_text(" ", strip=True)):
        return False
    if re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+)[\.\)]?\s*.+", text):
        return True
    if re.match(r"^[가나다라마바사아자차카타파하]\.\s*.+", text):
        return True
    if re.fullmatch(r"[A-Za-z]\.\s*.+", text):
        return True
    return False


def _iter_document_split_blocks(body: Tag | BeautifulSoup) -> list[Tag]:
    blocks: list[Tag] = []
    seen_html: set[str] = set()
    relevant_tags = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "div", "section", "article", "ul", "ol", "figcaption"}
    for tag in body.find_all(list(relevant_tags), recursive=True):
        if tag.find_parent(list(relevant_tags)) is not None:
            continue
        if tag.find_parent("table") is not None and tag.name != "table":
            continue
        if tag.name in {"div", "section", "article"}:
            if tag.find(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "ul", "ol"], recursive=True):
                continue
        html_chunk = str(tag)
        if html_chunk in seen_html:
            continue
        seen_html.add(html_chunk)
        blocks.append(tag)
    return blocks


def _block_line_count(block: Tag) -> int:
    lines = _extract_lines_from_tag(block)
    if lines:
        return len(lines)
    text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()
    return 1 if text else 0


def _split_document_regions(soup: BeautifulSoup) -> tuple[Tag | BeautifulSoup, Tag | BeautifulSoup]:
    """Split document into TOC-focused region and body-focused region."""
    body = soup.body or soup
    blocks = _iter_document_split_blocks(body)
    if not blocks:
        return soup, soup

    def _normalized_block_text(block: Tag) -> str:
        return re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()

    def _is_still_toc_context(start_index: int) -> bool:
        lookahead = blocks[start_index + 1 : start_index + 5]
        if not lookahead:
            return False
        toc_like_count = 0
        for candidate in lookahead:
            candidate_text = _normalized_block_text(candidate)
            if not candidate_text:
                continue
            if (
                _looks_like_toc_line(candidate_text)
                or TRAILING_PAGE_PATTERN.search(candidate_text)
                or _block_has_toc_signal(candidate)
            ):
                toc_like_count += 1
        return toc_like_count >= 2

    toc_marker_idx: int | None = None
    for index, block in enumerate(blocks):
        normalized = _normalized_block_text(block)
        if TOC_HEADING_COMPACT_PATTERN.match(normalized):
            toc_marker_idx = index
            break
    if toc_marker_idx is None:
        for index, block in enumerate(blocks):
            normalized = _normalized_block_text(block)
            if not normalized or not _block_has_toc_signal(block):
                continue
            lookahead = blocks[index + 1 : index + 6]
            toc_like_count = 0
            for candidate in lookahead:
                candidate_text = _normalized_block_text(candidate)
                if not candidate_text:
                    continue
                if (
                    _looks_like_toc_line(candidate_text)
                    or TRAILING_PAGE_PATTERN.search(candidate_text)
                    or _block_has_toc_signal(candidate)
                ):
                    toc_like_count += 1
            if toc_like_count >= 1:
                toc_marker_idx = index
                break

    if toc_marker_idx is not None:
        split_at_block: int | None = None
        for index in range(toc_marker_idx + 1, len(blocks)):
            block = blocks[index]
            if "page-label" in " ".join(block.get("class", [])).lower():
                continue
            normalized = _normalized_block_text(block)
            if not normalized:
                continue
            if _looks_like_body_start_block(block) and not TRAILING_PAGE_PATTERN.search(normalized):
                if _is_still_toc_context(index):
                    continue
                split_at_block = index
                break
        if split_at_block is not None:
            toc_blocks = blocks[:split_at_block]
            body_blocks = blocks[split_at_block:]
            toc_region = BeautifulSoup("".join(str(block) for block in toc_blocks), "html.parser")
            body_region = BeautifulSoup("".join(str(block) for block in body_blocks), "html.parser")
            if body_region.get_text(" ", strip=True):
                return toc_region, body_region

    raw_lines = [line.strip() for line in body.get_text("\n", strip=True).splitlines() if line.strip()]
    boundary_line_idx: int | None = None
    if raw_lines:
        for index in range(len(raw_lines) - 1, -1, -1):
            line = re.sub(r"\s+", " ", raw_lines[index]).strip()
            if not line:
                continue
            if TRAILING_PAGE_PATTERN.search(line):
                boundary_line_idx = index
                break

    if boundary_line_idx is not None:
        toc_blocks: list[Tag] = []
        body_blocks: list[Tag] = []
        cumulative = -1
        split_at_block: int | None = None
        for index, block in enumerate(blocks):
            line_count = _block_line_count(block)
            if line_count <= 0:
                continue
            cumulative += line_count
            if cumulative >= boundary_line_idx:
                split_at_block = index
                break
        if split_at_block is not None:
            toc_blocks = blocks[: split_at_block + 1]
            body_blocks = blocks[split_at_block + 1 :]
            toc_region = BeautifulSoup("".join(str(block) for block in toc_blocks), "html.parser")
            body_region = BeautifulSoup("".join(str(block) for block in body_blocks), "html.parser")
            if body_region.get_text(" ", strip=True):
                return toc_region, body_region

    toc_parts: list[str] = []
    body_parts: list[str] = []
    seen_toc_signal = False
    body_started = False

    for block in blocks:
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()
        lower_text = text.lower()
        is_toc_signal = bool(
            TOC_HEADING_COMPACT_PATTERN.match(text)
            or TRAILING_PAGE_PATTERN.search(text)
            or (block.name in {"ul", "ol"} and _looks_like_toc_line(text))
            or (block.name == "table" and _looks_like_toc_line(text))
            or (block.name in {"p", "div", "section"} and _looks_like_toc_line(text))
        )
        is_body_start = _looks_like_body_start_block(block)

        if body_started:
            body_parts.append(str(block))
            continue

        if is_toc_signal:
            seen_toc_signal = True
            toc_parts.append(str(block))
            continue

        if seen_toc_signal and is_body_start:
            body_started = True
            body_parts.append(str(block))
            continue

        toc_parts.append(str(block))

    if not body_parts:
        # Secondary pass: if TOC and body are mixed inside one wrapper, split
        # on the first strong body heading after any TOC-like lines.
        toc_parts = []
        body_parts = []
        started_toc = False
        body_started = False
        for block in blocks:
            text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()
            if not text:
                continue
            is_toc_line = bool(
                TOC_HEADING_COMPACT_PATTERN.match(text)
                or TRAILING_PAGE_PATTERN.search(text)
                or _looks_like_toc_line(text)
            )
            is_body_start = _looks_like_body_start_block(block)
            if body_started:
                body_parts.append(str(block))
                continue
            if is_toc_line:
                started_toc = True
                toc_parts.append(str(block))
                continue
            if started_toc and is_body_start:
                body_started = True
                body_parts.append(str(block))
                continue
            if started_toc:
                toc_parts.append(str(block))
            else:
                toc_parts.append(str(block))
        if not body_parts:
            return soup, soup

    toc_region = BeautifulSoup("".join(toc_parts), "html.parser") if toc_parts else soup
    body_region = BeautifulSoup("".join(body_parts), "html.parser") if body_parts else soup
    return toc_region, body_region


def _extract_lines_from_tag(tag: Tag) -> list[str]:
    if tag.name in {"ul", "ol"}:
        lines: list[str] = []
        for li in tag.find_all("li", recursive=False):
            lines.extend(_extract_lines_from_tag(li))
        return [line for line in lines if line.strip()]
    if tag.name in {"td", "th"}:
        direct_block_tags = {"p", "div", "section", "article", "blockquote", "figcaption", "ul", "ol", "table", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
        block_children = [
            child
            for child in tag.children
            if isinstance(child, Tag) and child.name in direct_block_tags
        ]
        if block_children:
            lines: list[str] = []
            for child in block_children:
                lines.extend(_extract_lines_from_tag(child))
            if lines:
                return [line for line in lines if line.strip()]
    if tag.name == "li":
        direct_block_tags = {"p", "div", "section", "article", "blockquote", "figcaption", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
        block_children = [
            child
            for child in tag.children
            if isinstance(child, Tag) and child.name in direct_block_tags
        ]
        if block_children:
            lines: list[str] = []
            for child in block_children:
                lines.extend(_extract_lines_from_tag(child))
            if lines:
                return [line for line in lines if line.strip()]
    if tag.name == "table":
        lines: list[str] = []
        for row in tag.find_all("tr"):
            text = row.get_text(" ", strip=True)
            if text:
                lines.append(text)
        return lines
    if tag.name in {"p", "div", "section", "article", "blockquote", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6"}:
        text = tag.get_text("\n", strip=True)
        return [line.strip() for line in text.splitlines() if line.strip()]
    text = tag.get_text("\n", strip=True)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _normalize_toc_marker(value: str) -> str:
    return re.sub(r"[\s#:\-_.·•]+", "", value).strip().lower()


def _looks_like_toc_line(line: str) -> bool:
    compact = re.sub(r"\s+", " ", line).strip()
    if len(compact) < 4:
        return False
    if TOC_HEADING_PATTERN.match(compact):
        return False
    if TRAILING_PAGE_PATTERN.search(compact):
        return True
    if LEADING_NUMBER_PATTERN.search(compact) and re.search(r"\d{1,4}\s*$", compact):
        return True
    return False


def _looks_like_strict_toc_line(line: str) -> bool:
    compact = re.sub(r"\s+", " ", line).strip()
    if not compact:
        return False
    if _is_noise_title(compact):
        return False
    if not TRAILING_PAGE_PATTERN.search(compact):
        return False
    if _looks_like_body_start_block(BeautifulSoup(f"<div>{compact}</div>", "html.parser").div):
        return False
    return bool(_parse_toc_line(compact, fallback_level=2) or _parse_heading_like_line(compact, fallback_level=2))


def _reconstruct_toc_entries_from_lines(lines: list[str]) -> list[str]:
    heading_token_pattern = re.compile(
        r"^(?:[IVXLCDM]+\.?|제?\s*\d+\s*(?:장|절|항)\.?|\d+(?:\.\d+)*\.?|[가나다라마바사아자차카타파하]\.|[A-Za-z]\.)$"
    )
    entries: list[str] = []
    current: list[str] = []
    started = False

    for raw in lines:
        compact = re.sub(r"\s+", " ", raw).strip()
        if not compact or _is_noise_title(compact):
            continue
        normalized = _normalize_toc_marker(compact.lower().strip("# ").strip())
        if normalized in {"목차", "목 차", "contents", "tableofcontents"}:
            started = True
            continue

        is_heading_token = bool(heading_token_pattern.fullmatch(compact))
        is_page_only = bool(re.fullmatch(r"\d{1,4}", compact))
        is_leader_only = bool(re.fullmatch(r"[.\-_·•\s]+", compact))

        if not started and is_heading_token:
            started = True

        if not started:
            continue

        if current and is_heading_token and len(current) >= 2:
            entries.append(" ".join(current))
            current = []

        current.append(compact)

        if is_page_only or is_leader_only or TRAILING_PAGE_PATTERN.search(compact):
            entries.append(" ".join(current))
            current = []

    if current:
        entries.append(" ".join(current))
    return entries


def _score_toc_line(line: str) -> int:
    score = 0
    if re.search(r"(?:\d+\.)+\d*|\b[IVX]+\b", line):
        score += 1
    if re.search(r"[.\-_·•]{2,}\s*\d{1,4}$", line):
        score += 2
    if re.search(r"\d{1,4}\s*$", line):
        score += 1
    if len(line.split()) >= 2:
        score += 1
    return score


def _is_noise_title(title: str) -> bool:
    compact = re.sub(r"\s+", " ", title).strip()
    if len(compact) < 2:
        return True
    if compact.isdigit():
        return True
    if compact.lower() in {"page", "contents", "목차"}:
        return True
    if not re.search(r"[가-힣A-Za-z]", compact):
        return True
    return False


def _has_sequence_prefix(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return False
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇※]+\s*", "", compact)
    return bool(
        re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*|[IVXLCDM]+)[\.\)]?\s+", compact, re.IGNORECASE)
        or re.match(r"^[가나다라마바사아자차카타파하][\.\)]\s+", compact)
    )


def _parse_toc_line(line: str, fallback_level: int = 1) -> TocItem | None:
    compact = re.sub(r"\s+", " ", line).strip()
    if not compact or len(compact) < 2:
        return None
    if TOC_HEADING_PATTERN.match(compact):
        return None
    if not _looks_like_toc_line(compact):
        return None
    if re.match(r"^[\-\*•·▪■◆▶◦○□◇※]\s*", compact) and not _has_sequence_prefix(compact):
        return None

    matched = TOC_LINE_PATTERN.match(compact)
    if not matched:
        return None

    title = matched.group("title") or ""
    title = re.sub(r"[.\-_·•\s]+\d{1,4}$", "", title).strip()
    title = re.sub(r"\s+", " ", title).strip(" -._·•")
    if _is_noise_title(title):
        return None

    page_idx = _parse_page(matched.group("page"))
    level = _extract_level_from_number(matched.group("number"), fallback=fallback_level)
    anchor = _anchor_from_text(title)
    return TocItem(level=level, title=title, anchor=anchor, page_idx=page_idx, page_estimate=page_idx)


def _parse_heading_like_line(line: str, fallback_level: int = 1) -> TocItem | None:
    compact = re.sub(r"\s+", " ", line).strip()
    if not compact:
        return None
    had_bullet_prefix = bool(re.match(r"^[#\-*•·▪■◆▶◦○□◇※\s]+", compact))
    compact = re.sub(r"^[#\-*•·▪■◆▶◦○□◇※\s]+", "", compact)
    compact = re.sub(r"[.\-_·•\s]{2,}\d{1,4}\s*$", "", compact).strip()
    if _is_noise_title(compact):
        return None
    heading_match = re.match(
        r"^(?P<number>(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*|[IVXLCDM]+|[A-Za-z])[\.\)]?)\s+(?P<title>.+)$",
        compact,
    )
    if heading_match:
        number = heading_match.group("number")
        title = heading_match.group("title").strip()
        level = min(max(_extract_level_from_number(number, fallback=fallback_level), 1), 3)
        return TocItem(level=level, title=compact, anchor=_anchor_from_text(compact, has_bullet_prefix=had_bullet_prefix), page_idx=None)
    if had_bullet_prefix and not _has_sequence_prefix(compact):
        return None
    if re.match(r"^[가나다라마바사아자차카타파하]\.", compact):
        return TocItem(level=min(max(fallback_level, 1), 3), title=compact, anchor=_anchor_from_text(compact, has_bullet_prefix=had_bullet_prefix), page_idx=None)
    if re.match(r"^[A-Za-z]\.", compact):
        return TocItem(level=min(max(fallback_level, 1), 3), title=compact, anchor=_anchor_from_text(compact, has_bullet_prefix=had_bullet_prefix), page_idx=None)
    if re.match(r"^\d+(?:\.\d+)+\s+", compact):
        return TocItem(level=min(max(fallback_level, 1), 3), title=compact, anchor=_anchor_from_text(compact, has_bullet_prefix=had_bullet_prefix), page_idx=None)
    return None


def _split_numbered_heading_body(text: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return "", ""
    match = re.match(r"^(?P<prefix>(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*|[IVXLCDM]+|[A-Za-z])[\.\)]?)\s+(?P<rest>.+)$", compact, flags=re.IGNORECASE)
    if not match:
        return compact, ""

    prefix = re.sub(r"\s+", " ", match.group("prefix")).strip()
    rest = re.sub(r"\s+", " ", match.group("rest")).strip()
    if not prefix or not rest:
        return compact, ""

    body_start_pattern = re.compile(
        r"\s+(?:본|해당|당행|제안|추진|운영|구축|관리|제공|설명|평가|기술|요청|필요|필수)\b"
    )
    body_match = body_start_pattern.search(rest)
    if body_match and body_match.start() >= 4:
        heading_rest = re.sub(r"\s+", " ", rest[: body_match.start()]).strip()
        body = re.sub(r"\s+", " ", rest[body_match.start() + 1 :]).strip()
        if heading_rest and body:
            return f"{prefix} {heading_rest}".strip(), body

    return f"{prefix} {rest}".strip(), ""


def _split_combined_heading_line(line: str) -> list[str]:
    compact = re.sub(r"\s+", " ", line).strip()
    if not compact:
        return []

    body_heading_pattern = (
        r"(?:"
        r"제?\s*\d+\s*(?:장|절|항)"
        r"|\d+(?:\.\d+)+[\.\)]?"
        r"|[IVXLCDM]+[\.\)]?"
        r"|[가나다라마바사아자차카타파하]\."
        r")\s+"
    )
    split_match = re.search(
        rf"(?P<left>.+?[.\-_·•\s]{{2,}}\d{{1,4}})\s+(?P<right>{body_heading_pattern}.+)",
        compact,
    )
    if split_match:
        return [split_match.group("left").strip(), split_match.group("right").strip()]
    return [compact]


def _detect_txt_toc_style(txt_text: str) -> str:
    text = (txt_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n") if re.sub(r"\s+", " ", line).strip()]
    if not lines:
        return "document"

    leader_lines = 0
    page_lines = 0
    short_heading_lines = 0
    numbered_heading_lines = 0
    for line in lines[:240]:
        if TRAILING_PAGE_PATTERN.search(line):
            page_lines += 1
        if _looks_like_toc_line(line):
            leader_lines += 1
        if _parse_heading_like_line(line, fallback_level=2):
            numbered_heading_lines += 1
        if _looks_like_ppt_toc_line(line):
            short_heading_lines += 1

    doc_score = page_lines * 3 + leader_lines * 2 + numbered_heading_lines
    ppt_score = short_heading_lines * 2 + max(0, 8 - page_lines)
    if ppt_score > doc_score:
        return "ppt"
    return "document"


def _looks_like_ppt_toc_line(line: str) -> bool:
    compact = re.sub(r"\s+", " ", (line or "")).strip()
    if not compact or _is_noise_title(compact):
        return False
    if TOC_HEADING_PATTERN.match(compact):
        return False
    if _parse_toc_line(compact, fallback_level=2):
        return True
    if _parse_heading_like_line(compact, fallback_level=2):
        return True
    if len(compact) > 42:
        return False
    if re.search(r"[.\-_·•]{2,}\s*\d{1,4}\s*$", compact):
        return True
    if re.search(r"\d{1,4}\s*$", compact):
        return True
    if re.match(r"^[\-\*•·▪■◆▶◦○□◇※]\s+", compact):
        return True
    if re.match(r"^(?:목차|contents|table of contents)\b", compact, re.IGNORECASE):
        return True
    if re.search(r"[가-힣A-Za-z]", compact) and not re.search(r"[.!?。?!]$", compact):
        token_count = len([token for token in compact.split() if token])
        if token_count <= 7 and len(compact) <= 30:
            return True
    return False


def _classify_page_idx(page_idx: int | None, toc_end_idx: int, body_start_idx: int) -> str | None:
    if page_idx is None:
        return None
    if page_idx <= toc_end_idx:
        return "toc"
    if page_idx >= body_start_idx:
        return "body"
    return None


def _extract_toc_from_area(region: Tag | BeautifulSoup) -> list[TocItem]:
    toc_items: list[TocItem] = []

    for heading in region.find_all(_is_toc_heading):
        fallback_level = int((heading.name or "h1")[1])
        cursor = heading.find_next_sibling()
        scanned = 0
        misses = 0
        while cursor and scanned < 12:
            if isinstance(cursor, Tag):
                if re.fullmatch(r"h[1-6]", cursor.name or "") and not _is_toc_heading(cursor):
                    break
                lines = _extract_lines_from_tag(cursor)
                line_hits = 0
                for line in lines:
                    for fragment in _split_combined_heading_line(line):
                        item = _parse_toc_line(fragment, fallback_level=fallback_level)
                        if item:
                            toc_items.append(item)
                            line_hits += 1
                            continue
                        if _looks_like_strict_toc_line(fragment):
                            item = _parse_heading_like_line(fragment, fallback_level=fallback_level)
                            if item and not _looks_like_body_start_block(cursor):
                                toc_items.append(item)
                                line_hits += 1
                if line_hits == 0:
                    misses += 1
                else:
                    misses = 0
                if misses >= 2 and toc_items:
                    break
            cursor = cursor.find_next_sibling()
            scanned += 1

    if toc_items:
        return toc_items

    # Secondary fallback: reconstruct TOC entries from flattened text tokens.
    text_lines = [line.strip() for line in region.get_text("\n", strip=True).splitlines() if line.strip()]
    if text_lines:
        for raw in _reconstruct_toc_entries_from_lines(text_lines[:220]):
            for fragment in _split_combined_heading_line(raw):
                parsed = _parse_toc_line(fragment, fallback_level=2)
                if not parsed:
                    parsed = _parse_heading_like_line(fragment, fallback_level=2)
                if parsed:
                    toc_items.append(parsed)

    if toc_items:
        return _dedup_toc_items(toc_items)

    # Fallback: if there is no explicit TOC heading, search dense list/table blocks.
    for tag in region.find_all(["ul", "ol", "table", "div", "section"]):
        lines = _extract_lines_from_tag(tag)
        if len(lines) < 3:
            continue
        parsed = [item for line in lines for item in (_parse_toc_line(fragment) for fragment in _split_combined_heading_line(line)) if item]
        if len(parsed) >= 3 and sum(1 for line in lines[: min(len(lines), 8)] if TRAILING_PAGE_PATTERN.search(line)) >= 2:
            return parsed

    return []


def _extract_body_headings(region: Tag | BeautifulSoup) -> list[TocItem]:
    toc_items: list[TocItem] = []

    for index, tag in enumerate(region.find_all(re.compile(r"^(?:h[1-6]|li|p)$"))):
        if _is_toc_heading(tag):
            continue
        if tag.find_parent(re.compile(r"^(?:h[1-6]|li|p|table)$")) is not None:
            continue

        raw_title = tag.get_text(" ", strip=True)
        if _is_noise_title(raw_title):
            continue

        title = raw_title
        body_text = ""
        if tag.name in {"li", "p"}:
            split_title, split_body = _split_numbered_heading_body(raw_title)
            if split_body:
                title = split_title
                body_text = split_body

        if not _has_sequence_prefix(title):
            if tag.name in {"li", "p"}:
                heading_like = _parse_heading_like_line(title, fallback_level=2)
                if heading_like is None:
                    continue
                title = heading_like.title
            else:
                continue

        if body_text and _is_noise_title(title):
            continue

        anchor = tag.get("id") or _anchor_from_text(f"{index}-{title}")
        tag["id"] = anchor
        level = min(int(tag.name[1]) if tag.name.startswith("h") else 3, 3)
        toc_items.append(
            TocItem(
                level=level,
                title=title,
                anchor=anchor,
                page_idx=_extract_page_from_context(tag),
            )
        )

    return toc_items


def _is_toc_like_item(item: TocItem) -> bool:
    title = re.sub(r"\s+", " ", item.title).strip()
    if not title:
        return False
    if TOC_HEADING_COMPACT_PATTERN.match(title):
        return True
    if _looks_like_toc_line(title):
        return True
    if _parse_toc_line(title, fallback_level=item.level or 2):
        return True
    if _parse_heading_like_line(title, fallback_level=item.level or 2):
        return True
    return False


def _split_toc_prefix_from_body_items(items: list[TocItem]) -> tuple[list[TocItem], list[TocItem]]:
    if not items:
        return [], []
    area: list[TocItem] = []
    body: list[TocItem] = []
    in_body = False
    for index, item in enumerate(items):
        title = re.sub(r"\s+", " ", item.title).strip()
        normalized = _normalize_toc_marker(title.lower().strip("# ").strip())
        is_heading_intro = normalized in {"목차", "contents", "tableofcontents"}
        if not in_body and (is_heading_intro or _is_toc_like_item(item)):
            area.append(item)
            continue
        # Once a strong body heading appears, keep the rest as body.
        if not in_body and re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)+)\s+", title):
            in_body = True
        if in_body:
            body.append(item)
        elif area:
            # If we already have a TOC prefix and hit a non-TOC line, move onward to body.
            in_body = True
            body.append(item)
        else:
            body.append(item)
    return area, body


def _merge_toc_items(area_items: list[TocItem], body_items: list[TocItem]) -> list[TocItem]:
    scored: dict[str, tuple[int, TocItem]] = {}
    body_by_title = {_normalize_title(item.title): item for item in body_items}

    for order, item in enumerate(area_items):
        normalized = _normalize_title(item.title)
        if not normalized:
            continue
        score = 30
        candidate = item
        body_match = body_by_title.get(normalized)
        if body_match:
            # Strong signal: appears in both TOC region and body headings.
            score += 40
            candidate = TocItem(
                level=item.level or body_match.level,
                title=body_match.title,
                anchor=body_match.anchor,
                page_idx=body_match.page_idx if body_match.page_idx is not None else item.page_idx,
                page_estimate=body_match.page_estimate if body_match.page_estimate is not None else item.page_estimate,
            )
        if item.page_idx is not None or item.page_estimate is not None:
            score += 10
        score += max(0, 10 - min(order, 10))

        previous = scored.get(normalized)
        if not previous or score > previous[0]:
            scored[normalized] = (score, candidate)

    for order, item in enumerate(body_items):
        normalized = _normalize_title(item.title)
        if not normalized:
            continue
        score = 20
        if item.page_idx is not None or item.page_estimate is not None:
            score += 5
        score += max(0, 8 - min(order, 8))

        previous = scored.get(normalized)
        if not previous or score > previous[0]:
            scored[normalized] = (score, item)

    body_order = {_normalize_title(item.title): index for index, item in enumerate(body_items)}
    area_order = {_normalize_title(item.title): index for index, item in enumerate(area_items)}
    merged = [entry[1] for entry in sorted(
        scored.values(),
        key=lambda entry: (
            body_order.get(_normalize_title(entry[1].title), 9999),
            area_order.get(_normalize_title(entry[1].title), 9999),
            entry[1].page_idx if entry[1].page_idx is not None else (entry[1].page_estimate if entry[1].page_estimate is not None else 9999),
            entry[1].level,
            entry[1].title,
        ),
    )]
    # Prefer explicit TOC-area titles first, then keep body headings that weren't
    # already covered by the TOC area. This avoids over-deduping sparse Samsung
    # Card style documents where the same heading appears in multiple places.
    if not area_items:
        return merged
    area_keys = {_normalize_title(item.title) for item in area_items if _normalize_title(item.title)}
    extras = [item for item in body_items if _normalize_title(item.title) not in area_keys]
    return merged + extras


def extract_toc_views(html: str) -> tuple[list[TocItem], list[TocItem], list[TocItem]]:
    soup = BeautifulSoup(html, "html.parser")
    toc_region, body_region = _split_document_regions(soup)
    area_items = _extract_toc_from_area(toc_region)
    body_items = _extract_body_headings(body_region)
    if body_items:
        body_toc_prefix, body_tail = _split_toc_prefix_from_body_items(body_items)
        if body_toc_prefix:
            area_items = _dedup_toc_items(area_items + body_toc_prefix)
            body_items = body_tail
    if not area_items and body_items:
        area_items, body_items = _split_toc_prefix_from_body_items(body_items)
    merged_items = _merge_toc_items(area_items, body_items)
    if merged_items and len(merged_items) >= 5 and not body_items:
        split_toc_html, split_body_html = split_toc_body_html_by_toc_items(html, merged_items)
        if split_body_html.strip():
            split_toc_region = BeautifulSoup(split_toc_html, "html.parser")
            split_body_region = BeautifulSoup(split_body_html, "html.parser")
            split_area_items = _extract_toc_from_area(split_toc_region)
            split_body_items = _extract_body_headings(split_body_region)
            if split_body_items:
                if split_body_items:
                    body_toc_prefix, body_tail = _split_toc_prefix_from_body_items(split_body_items)
                    if body_toc_prefix:
                        split_area_items = _dedup_toc_items(split_area_items + body_toc_prefix)
                        split_body_items = body_tail
                if not split_area_items and split_body_items:
                    split_area_items, split_body_items = _split_toc_prefix_from_body_items(split_body_items)
                area_items = split_area_items
                body_items = split_body_items
                merged_items = _merge_toc_items(area_items, body_items)
    return area_items, body_items, merged_items


def split_toc_body_html(html: str) -> tuple[str, str]:
    """Return HTML fragments for the TOC region and the body region."""
    soup = BeautifulSoup(html, "html.parser")
    toc_region, body_region = _split_document_regions(soup)
    return str(toc_region), str(body_region)


def split_toc_body_html_by_toc_items(html: str, toc_items: list[TocItem]) -> tuple[str, str]:
    """Split HTML into TOC/body fragments using final TOC titles as the boundary hint."""
    if not html.strip() or not toc_items:
        return split_toc_body_html(html)

    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    blocks = _iter_document_split_blocks(body)
    if not blocks:
        return split_toc_body_html(html)

    anchor_titles: list[str] = []
    for item in toc_items:
        title = _normalize_title(item.title)
        if title and "목차" not in title.replace(" ", ""):
            anchor_titles.append(item.anchor or _anchor_from_text(title))
        if len(anchor_titles) >= 5:
            break
    if not anchor_titles:
        return split_toc_body_html(html)

    split_idx = _find_toc_anchor_split_index(blocks, anchor_titles)
    if split_idx is None:
        return split_toc_body_html(html)

    toc_region = BeautifulSoup("".join(str(block) for block in blocks[:split_idx]), "html.parser")
    body_region = BeautifulSoup("".join(str(block) for block in blocks[split_idx:]), "html.parser")
    if body_region.get_text(" ", strip=True):
        return str(toc_region), str(body_region)
    return split_toc_body_html(html)


def _find_toc_anchor_split_index(blocks: list[Tag], anchor_titles: list[str]) -> int | None:
    if not blocks or not anchor_titles:
        return None

    if len(anchor_titles) >= 5:
        sequence_split_idx = _match_anchor_sequence_split_index(blocks, anchor_titles[:5], max_gap=0)
        if sequence_split_idx is not None:
            return sequence_split_idx

    first_title = _anchor_from_text(anchor_titles[0])
    if not first_title:
        return None
    excluded_indices = _collect_toc_region_indices(blocks)
    search_blocks = [(idx, block) for idx, block in enumerate(blocks) if idx not in excluded_indices]
    positions: list[int] = []
    for idx, block in search_blocks:
        block_text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()
        compact = _anchor_from_text(re.sub(r"[.\-_·•\s]{2,}\d{1,4}\s*$", "", block_text).strip())
        if not compact:
            continue
        if compact == first_title or compact.startswith(f"{first_title}-") or first_title.startswith(f"{compact}-"):
            positions.append(idx)

    if not positions:
        return None
    return positions[0]


def _match_anchor_sequence_split_index(
    blocks: list[Tag],
    anchor_titles: list[str],
    *,
    max_gap: int = 0,
) -> int | None:
    normalized_titles = [_anchor_from_text(title) for title in anchor_titles[:5]]
    normalized_titles = [title for title in normalized_titles if title]
    if not blocks or len(normalized_titles) < 2:
        return None

    excluded_indices = _collect_toc_region_indices(blocks)
    search_blocks = [(idx, block) for idx, block in enumerate(blocks) if idx not in excluded_indices]
    if len(search_blocks) < len(normalized_titles):
        return None

    anchor_cache: dict[int, str] = {}

    def _block_anchor(idx: int, block: Tag) -> str:
        cached = anchor_cache.get(idx)
        if cached is not None:
            return cached
        block_text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)).strip()
        block_text = re.sub(r"[.\-_·•\s]{2,}\d{1,4}\s*$", "", block_text).strip()
        anchor = _anchor_from_text(block_text)
        anchor_cache[idx] = anchor
        return anchor

    for start_pos, (start_idx, start_block) in enumerate(search_blocks):
        if _block_anchor(start_idx, start_block) != normalized_titles[0]:
            continue
        prev_idx = start_idx
        matched = True
        for expected_title in normalized_titles[1:]:
            found_idx: int | None = None
            for cand_idx, cand_block in search_blocks[start_pos + 1:]:
                if cand_idx <= prev_idx:
                    continue
                if cand_idx - prev_idx > max_gap + 1:
                    break
                if _block_anchor(cand_idx, cand_block) == expected_title:
                    found_idx = cand_idx
                    break
            if found_idx is None:
                matched = False
                break
            prev_idx = found_idx
        if matched:
            return start_idx
    return None


def extract_toc(html: str) -> list[TocItem]:
    _, _, merged_items = extract_toc_views(html)
    return merged_items


def _dedup_toc_items(items: list[TocItem]) -> list[TocItem]:
    dedup: list[TocItem] = []
    seen: set[str] = set()
    for item in items:
        key = _normalize_title(item.title)
        if not key or key in seen:
            continue
        dedup.append(item)
        seen.add(key)
    return dedup


def _trim_body_items_without_explicit_toc(items: list[TocItem]) -> list[TocItem]:
    trimmed: list[TocItem] = []
    for item in items:
        if item.level > 3:
            continue
        trimmed.append(_clone_toc_item(item))
    return trimmed


def extract_toc_views_from_markdown(markdown_text: str) -> tuple[list[TocItem], list[TocItem], list[TocItem]]:
    toc_area_items: list[TocItem] = []
    toc_body_items: list[TocItem] = []
    if not markdown_text:
        return toc_area_items, toc_body_items, []

    in_toc_section = False
    body_started = False
    fallback_level = 2
    for line in markdown_text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        fragments = _split_combined_heading_line(raw)
        if len(fragments) > 1:
            for fragment in fragments:
                parsed = _parse_toc_line(fragment, fallback_level=fallback_level)
                if not parsed:
                    parsed = _parse_heading_like_line(fragment, fallback_level=fallback_level)
                if parsed:
                    bucket = _classify_page_idx(parsed.page_idx if parsed.page_idx is not None else parsed.page_estimate, toc_end_idx=3, body_start_idx=4)
                    if body_started or bucket == "body":
                        body_started = True
                        toc_body_items.append(parsed)
                    elif bucket == "toc":
                        toc_area_items.append(parsed)
                        in_toc_section = True
                    elif in_toc_section:
                        toc_area_items.append(parsed)
                    else:
                        toc_body_items.append(parsed)
            continue
        normalized_line = _normalize_toc_marker(raw.lower().strip("# ").strip())
        if normalized_line in {"목차", "contents", "tableofcontents"}:
            in_toc_section = True
            continue

        if raw.startswith("#"):
            hashes = len(raw) - len(raw.lstrip("#"))
            fallback_level = min(max(hashes, 1), 6)
            title = raw[hashes:].strip()
            if _is_noise_title(title):
                continue
            item = TocItem(level=fallback_level, title=title, anchor=_anchor_from_text(title), page_idx=None)
            if not body_started and in_toc_section:
                toc_area_items.append(item)
            else:
                body_started = True
                toc_body_items.append(item)
            continue

        candidate = raw[2:].strip() if raw.startswith(("- ", "* ", "+ ")) else raw
        if (
            not body_started
            and not raw.startswith("#")
            and not _looks_like_toc_line(candidate)
            and _parse_heading_like_line(candidate, fallback_level=fallback_level)
        ):
            body_started = True
        parsed = _parse_toc_line(candidate, fallback_level=fallback_level)
        if not parsed:
            parsed = _parse_heading_like_line(candidate, fallback_level=fallback_level)
        if parsed:
            bucket = _classify_page_idx(parsed.page_idx if parsed.page_idx is not None else parsed.page_estimate, toc_end_idx=3, body_start_idx=4)
            if body_started or bucket == "body":
                body_started = True
                toc_body_items.append(parsed)
            elif bucket == "toc":
                toc_area_items.append(parsed)
                in_toc_section = True
            elif in_toc_section:
                toc_area_items.append(parsed)
            else:
                toc_body_items.append(parsed)

    toc_area_items = _dedup_toc_items(toc_area_items)
    toc_body_items = _dedup_toc_items(toc_body_items)
    if not toc_area_items:
        toc_body_items = _trim_body_items_without_explicit_toc(toc_body_items)
    merged_items = _merge_toc_items(toc_area_items, toc_body_items)
    return toc_area_items, toc_body_items, merged_items


def extract_toc_from_markdown(markdown_text: str) -> list[TocItem]:
    _, _, merged_items = extract_toc_views_from_markdown(markdown_text)
    return merged_items


def extract_toc_views_from_json_text(json_text: str) -> tuple[list[TocItem], list[TocItem], list[TocItem]]:
    toc_area_items: list[TocItem] = []
    toc_body_items: list[TocItem] = []
    if not json_text:
        return toc_area_items, toc_body_items, []

    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return toc_area_items, toc_body_items, []

    doc_title = ""
    if isinstance(payload, dict):
        raw_doc_title = payload.get("title")
        if isinstance(raw_doc_title, str):
            doc_title = _normalize_title(raw_doc_title)

    candidates: list[tuple[tuple[str, ...], int | None, int | None, int, str]] = []

    def walk(value: object, path: tuple[str, ...] = (), fallback_level: int = 2, fallback_page: int | None = None) -> None:
        if isinstance(value, dict):
            title = None
            for key in ("title", "heading", "section_title", "name", "text", "content"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    title = candidate.strip()
                    break

            level = fallback_level
            for key in ("level", "heading_level", "text_level"):
                candidate = value.get(key)
                if isinstance(candidate, int):
                    level = min(max(candidate, 1), 6)
                    break

            page_idx = fallback_page
            page_estimate = fallback_page
            for key in ("page_idx", "page", "page_no", "page number"):
                candidate = value.get(key)
                if isinstance(candidate, int):
                    page_idx = candidate
                    page_estimate = page_idx
                    break
                if isinstance(candidate, str) and candidate.isdigit():
                    candidate_int = int(candidate)
                    page_idx = candidate_int
                    page_estimate = page_idx
                    break
            if page_idx is None:
                for key in ("page_estimate", "page_estimated", "estimated_page"):
                    candidate = value.get(key)
                    if isinstance(candidate, int):
                        page_estimate = candidate if candidate > 0 else None
                        break
                    if isinstance(candidate, str) and candidate.isdigit():
                        candidate_int = int(candidate)
                        page_estimate = candidate_int if candidate_int > 0 else None
                        break

            if title and (page_idx is not None or page_estimate is not None):
                candidates.append((path, page_idx, page_estimate, level, title))

            for key in ("kids", "source", "children", "items", "blocks"):
                nested = value.get(key)
                if nested is not None:
                    walk(nested, path=path + (str(key),), fallback_level=level or fallback_level, fallback_page=page_idx if page_idx is not None else page_estimate)
            return

        if isinstance(value, list):
            for nested in value:
                walk(nested, path=path, fallback_level=fallback_level, fallback_page=fallback_page)

    walk(payload)

    for path, page_idx, page_estimate, level, title in candidates:
        compact = re.sub(r"\s+", " ", title).strip()
        if not compact or _is_noise_title(compact):
            continue
        compact_norm = _normalize_title(compact)
        if page_idx == 0:
            continue
        if compact_norm and doc_title and compact_norm == doc_title:
            continue
        if compact_norm in {_normalize_title("목 차"), _normalize_title("목차")}:
            continue
        path_lower = tuple(part.lower() for part in path)
        this_is_toc_area = any(
            token in part for part in path_lower for token in ("toc", "contents", "tableofcontents", "table_of_contents", "목차")
        )
        fragments = _split_combined_heading_line(compact)
        for fragment in fragments:
            parsed = _parse_toc_line(fragment, fallback_level=level)
            if parsed:
                if parsed.page_idx is None:
                    parsed.page_estimate = page_idx if page_idx is not None else page_estimate
                page_for_bucket = parsed.page_idx if parsed.page_idx is not None else parsed.page_estimate
                if this_is_toc_area or (page_for_bucket is not None and page_for_bucket <= 3):
                    toc_area_items.append(parsed)
                else:
                    toc_body_items.append(parsed)
                continue
            parsed_heading = _parse_heading_like_line(fragment, fallback_level=level)
            if parsed_heading:
                parsed_heading.page_estimate = page_idx if page_idx is not None else page_estimate
                page_for_bucket = page_idx if page_idx is not None else page_estimate
                if this_is_toc_area or (page_for_bucket is not None and page_for_bucket <= 3):
                    toc_area_items.append(parsed_heading)
                else:
                    toc_body_items.append(parsed_heading)
                continue
            if not _is_noise_title(fragment):
                item = TocItem(level=level, title=fragment, anchor=_anchor_from_text(fragment), page_estimate=page_idx if page_idx is not None else page_estimate)
                page_for_bucket = page_idx if page_idx is not None else page_estimate
                if this_is_toc_area or (page_for_bucket is not None and page_for_bucket <= 3):
                    toc_area_items.append(item)
                else:
                    toc_body_items.append(item)
    toc_area_items = _dedup_toc_items(toc_area_items)
    toc_body_items = _dedup_toc_items(toc_body_items)
    if not toc_area_items:
        toc_body_items = _trim_body_items_without_explicit_toc(toc_body_items)
    merged_items = _merge_toc_items(toc_area_items, toc_body_items)
    return toc_area_items, toc_body_items, merged_items


def extract_toc_from_json_text(json_text: str) -> list[TocItem]:
    _, _, merged_items = extract_toc_views_from_json_text(json_text)
    return merged_items


def _extract_toc_views_from_txt_document(txt_text: str) -> tuple[list[TocItem], list[TocItem], list[TocItem]]:
    toc_area_items: list[TocItem] = []
    toc_body_items: list[TocItem] = []
    if not txt_text:
        return toc_area_items, toc_body_items, []

    in_toc_section = False
    fallback_level = 2
    lines = txt_text.splitlines()
    for line_idx, line in enumerate(lines):
        raw = line.strip()
        if not raw:
            continue
        fragments = _split_combined_heading_line(raw)
        if len(fragments) > 1:
            for fragment in fragments:
                parsed = _parse_toc_line(fragment, fallback_level=fallback_level)
                if not parsed:
                    parsed = _parse_heading_like_line(fragment, fallback_level=fallback_level)
                if parsed:
                    if parsed.page_idx is None and parsed.page_estimate is None:
                        parsed.page_estimate = _find_next_page_marker_idx(lines, line_idx)
                    page_for_bucket = parsed.page_idx if parsed.page_idx is not None else parsed.page_estimate
                    bucket = _classify_page_idx(page_for_bucket, toc_end_idx=3, body_start_idx=4)
                    if bucket == "toc":
                        toc_area_items.append(parsed)
                        in_toc_section = True
                        continue
                    if bucket == "body":
                        toc_body_items.append(parsed)
                        in_toc_section = False
                        continue
                    if in_toc_section:
                        toc_area_items.append(parsed)
                    else:
                        toc_body_items.append(parsed)
            continue

        normalized_line = _normalize_toc_marker(raw.lower().strip("#*-+ \t"))
        if normalized_line in {"목차", "contents", "tableofcontents"}:
            in_toc_section = True
            continue

        parsed = _parse_toc_line(raw, fallback_level=fallback_level)
        if not parsed:
            parsed = _parse_heading_like_line(raw, fallback_level=fallback_level)
        if parsed:
            if parsed.page_idx is None and parsed.page_estimate is None:
                parsed.page_estimate = _find_next_page_marker_idx(lines, line_idx)
            page_for_bucket = parsed.page_idx if parsed.page_idx is not None else parsed.page_estimate
            bucket = _classify_page_idx(page_for_bucket, toc_end_idx=3, body_start_idx=4)
            if bucket == "toc":
                toc_area_items.append(parsed)
                in_toc_section = True
                continue
            if bucket == "body":
                toc_body_items.append(parsed)
                in_toc_section = False
                continue
            if in_toc_section:
                toc_area_items.append(parsed)
            else:
                toc_body_items.append(parsed)
            continue

        if re.match(r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)+)\s+", raw) and not _is_noise_title(raw):
            item = TocItem(
                level=fallback_level,
                title=raw,
                anchor=_anchor_from_text(raw),
                page_estimate=_find_next_page_marker_idx(lines, line_idx),
            )
            if in_toc_section:
                toc_area_items.append(item)
            else:
                toc_body_items.append(item)

    toc_area_items = _dedup_toc_items(toc_area_items)
    toc_body_items = _dedup_toc_items(toc_body_items)
    merged_items = _merge_toc_items(toc_area_items, toc_body_items)
    return toc_area_items, toc_body_items, merged_items


def extract_toc_views_from_txt(txt_text: str) -> tuple[list[TocItem], list[TocItem], list[TocItem]]:
    return _extract_toc_views_from_txt_document(txt_text)


def extract_toc_from_txt(txt_text: str) -> list[TocItem]:
    _, _, merged_items = extract_toc_views_from_txt(txt_text)
    return merged_items


def _summarize_candidates_from_text_lines(lines: list[str], max_items: int = 120) -> str:
    summary: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        compact = re.sub(r"\s+", " ", raw).strip()
        if not compact or _is_noise_title(compact):
            continue
        parsed = _parse_toc_line(compact, fallback_level=2)
        if not parsed:
            parsed = _parse_heading_like_line(compact, fallback_level=2)
        if not parsed:
            continue
        title = re.sub(r"\s+", " ", parsed.title).strip()
        key = _normalize_title(title)
        if not key or key in seen:
            continue
        seen.add(key)
        summary_page = parsed.page_idx if parsed.page_idx is not None else parsed.page_estimate
        prefix = f"p{summary_page + 1}: " if summary_page is not None else ""
        summary.append(f"{prefix}{title}")
        if len(summary) >= max_items:
            break
    return "\n".join(summary)


def _summarize_document_lines(lines: list[str], max_items: int = 320) -> str:
    summary: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        compact = re.sub(r"\s+", " ", raw).strip()
        if not compact or _is_noise_title(compact):
            continue
        key = _normalize_title(compact)
        if not key or key in seen:
            continue
        seen.add(key)
        summary.append(compact)
        if len(summary) >= max_items:
            break
    return "\n".join(summary)


def summarize_markdown_for_toc(markdown_text: str, max_items: int = 120) -> str:
    if not markdown_text.strip():
        return ""
    lines = markdown_text.splitlines()
    candidates = [line for line in lines if line.strip() and (line.strip().startswith("#") or _looks_like_toc_line(line) or _parse_heading_like_line(line))]
    return _summarize_candidates_from_text_lines(candidates or lines, max_items=max_items)


def summarize_txt_for_toc(txt_text: str, max_items: int = 120) -> str:
    if not txt_text.strip():
        return ""
    lines = txt_text.splitlines()
    candidates = [line for line in lines if line.strip() and (_looks_like_toc_line(line) or _parse_heading_like_line(line))]
    return _summarize_candidates_from_text_lines(candidates or lines, max_items=max_items)


def summarize_markdown_for_document(markdown_text: str, max_items: int = 320) -> str:
    if not markdown_text.strip():
        return ""
    lines = markdown_text.splitlines()
    candidates = [line for line in lines if line.strip()]
    return _summarize_document_lines(candidates or lines, max_items=max_items)


def summarize_txt_for_document(txt_text: str, max_items: int = 320) -> str:
    if not txt_text.strip():
        return ""
    lines = txt_text.splitlines()
    candidates = [line for line in lines if line.strip()]
    return _summarize_document_lines(candidates or lines, max_items=max_items)


def summarize_json_for_toc(json_text: str, max_items: int = 120) -> str:
    if not json_text.strip():
        return ""
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return ""

    lines: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            title = None
            for key in ("title", "heading", "section_title", "name", "text", "content"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    title = candidate.strip()
                    break
            if title:
                lines.append(title)
            for key in ("kids", "source", "children", "items", "blocks"):
                nested = value.get(key)
                if nested is not None:
                    walk(nested)
            return
        if isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(payload)
    return _summarize_candidates_from_text_lines(lines, max_items=max_items)


def extract_sections(html: str, toc_items: list[TocItem]) -> list[Section]:
    soup = BeautifulSoup(html, "html.parser")
    sections: list[Section] = []
    headings = soup.find_all(re.compile(r"^h[1-6]$"))

    for index, heading in enumerate(headings):
        if _is_toc_heading(heading):
            continue

        title = heading.get_text(" ", strip=True)
        level = int(heading.name[1])
        anchor = heading.get("id") or _anchor_from_text(f"{index}-{title}")
        page_idx = _extract_page_from_context(heading)

        fragments: list[str] = []
        cursor = heading.parent if heading.parent and heading.parent.name == "section" else heading

        for sibling in cursor.next_siblings:
            name = getattr(sibling, "name", None)
            if name and re.fullmatch(r"h[1-6]", name):
                next_level = int(name[1])
                if next_level <= level:
                    break
            if getattr(sibling, "get_text", None):
                fragments.append(str(sibling))

        if cursor.name == "section":
            section_html = str(cursor) + "".join(fragments)
        else:
            section_html = str(heading) + "".join(fragments)

        section_soup = BeautifulSoup(section_html, "html.parser")
        text = section_soup.get_text("\n", strip=True)
        sections.append(
            Section(
                title=title,
                anchor=anchor,
                level=level,
                page_idx=page_idx,
                html=section_html,
                text=text,
            )
        )

    if sections:
        return sections

    # Fallback: when heading tags are sparse, synthesize sections from merged TOC titles.
    body_text = soup.get_text("\n", strip=True)
    for item in toc_items:
        if not item.title:
            continue
        sections.append(
            Section(
                title=item.title,
                anchor=item.anchor,
                level=item.level,
                page_idx=item.page_idx,
                html="",
                text=body_text,
            )
        )
    return sections
