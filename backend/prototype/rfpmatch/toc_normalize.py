"""TocItem 리스트 정규화 — 페이지 채움/조정, dedup, relevel, slugify. rfpmatch/shared_toc.py 이식.

순수 리스트 변환만 담는다(Streamlit UI 오케스트레이션 함수는 제외).
"""

from __future__ import annotations

import re

from .models import TocItem


def toc_items_to_rows(
    items: list[TocItem], page_source_map: dict[str, str] | None = None
) -> list[dict]:
    return [
        {
            "level": item.level,
            "title": item.title,
            "page_idx": item.page_idx if item.page_idx is not None else item.page_estimate,
            "page_estimate": item.page_estimate,
            "page_source": (page_source_map or {}).get(normalize_title_for_match(item.title), ""),
            "match_anchor": item.anchor,
        }
        for item in items
    ]


def build_page_source_map(
    toc_area_items: list[TocItem] | None = None,
    toc_body_items: list[TocItem] | None = None,
) -> dict[str, str]:
    source_map: dict[str, str] = {}
    for item in toc_area_items or []:
        key = normalize_title_for_match(item.title)
        if key and key not in source_map:
            source_map[key] = "TOC"
    for item in toc_body_items or []:
        key = normalize_title_for_match(item.title)
        if key and key not in source_map:
            source_map[key] = "body first marker"
    return source_map


def rows_to_toc_items(rows: list[dict]) -> list[TocItem]:
    updated_items: list[TocItem] = []
    for row in rows:
        title = str(row.get("title", "")).strip()
        if not title:
            continue
        if "목차" in re.sub(r"\s+", "", title).lower():
            continue
        if _is_unsequenced_bullet_title(title):
            continue
        try:
            level = int(row.get("level", 1))
        except (TypeError, ValueError):
            level = 1
        page_raw = row.get("page_idx", row.get("page", None))
        estimate_raw = row.get(
            "page_estimate", row.get("pageEstimate", row.get("page_estimated", None))
        )
        try:
            page_idx = int(page_raw) if page_raw not in (None, "", "None") else None
        except (TypeError, ValueError):
            page_idx = None
        try:
            page_estimate = int(estimate_raw) if estimate_raw not in (None, "", "None") else None
        except (TypeError, ValueError):
            page_estimate = None
        anchor_raw = row.get("anchor", row.get("match_anchor", row.get("anchor_text", None)))
        anchor = str(anchor_raw).strip() if anchor_raw not in (None, "", "None") else ""
        if not anchor:
            anchor = slugify(title)
        if page_idx is None and page_estimate is not None:
            page_idx = page_estimate
        updated_items.append(
            TocItem(
                level=min(max(level, 1), 3),
                title=title,
                page_idx=page_idx,
                page_estimate=page_estimate,
                anchor=anchor,
            )
        )
    return updated_items


def fill_missing_pages_from_neighbors(items: list[TocItem]) -> list[TocItem]:
    if not items:
        return items
    known_pages = sorted({item.page_idx for item in items if item.page_idx is not None})
    if len(known_pages) < 2:
        return items
    filled: list[TocItem] = []
    last_page: int | None = None
    next_pages: list[int | None] = [item.page_idx for item in items]

    def _next_known_page(start: int) -> int | None:
        for idx in range(start, len(next_pages)):
            if next_pages[idx] is not None:
                return next_pages[idx]
        return None

    for idx, item in enumerate(items):
        page_idx = item.page_idx
        if page_idx is None:
            page_idx = item.page_estimate
            if page_idx is None:
                page_idx = last_page
            if page_idx is None:
                page_idx = _next_known_page(idx + 1)
        if page_idx is not None:
            last_page = page_idx
        filled.append(
            TocItem(
                level=item.level,
                title=item.title,
                page_idx=page_idx,
                page_estimate=item.page_estimate,
                anchor=item.anchor,
            )
        )
    return filled


def insert_toc_item(items: list[TocItem], index: int, *, title: str = "새 항목") -> list[TocItem]:
    clamped_index = max(0, min(index, len(items)))
    placeholder = TocItem(
        level=1,
        title=title or "새 항목",
        page_idx=None,
        page_estimate=None,
        anchor=slugify(title or "새 항목"),
    )
    return [*items[:clamped_index], placeholder, *items[clamped_index:]]


def append_toc_item(items: list[TocItem], *, title: str = "새 항목") -> list[TocItem]:
    return [
        *items,
        TocItem(
            level=1,
            title=title or "새 항목",
            page_idx=None,
            page_estimate=None,
            anchor=slugify(title or "새 항목"),
        ),
    ]


def move_toc_item(items: list[TocItem], index: int, delta: int) -> list[TocItem]:
    if not items or index < 0 or index >= len(items) or delta == 0:
        return items
    target = index + delta
    if target < 0 or target >= len(items):
        return items
    moved = list(items)
    item = moved.pop(index)
    moved.insert(target, item)
    return moved


def drop_toc_heading_items(items: list[TocItem]) -> list[TocItem]:
    filtered: list[TocItem] = []
    exact_toc_titles = {"목차", "목 차", "contents", "table of contents"}

    for item in items:
        compact = re.sub(r"\s+", " ", (item.title or "")).strip().lower()
        if compact in exact_toc_titles:
            continue
        filtered.append(item)
    return filtered


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _is_unsequenced_bullet_title(title: str) -> bool:
    compact = normalize_text(title)
    if not compact:
        return False
    if not re.match(r"^[\-\*•·▪■◆▶◦○□◇※]", compact):
        return False
    stripped = re.sub(r"^[\-\*•·▪■◆▶◦○□◇※]+\s*", "", compact)
    return not bool(
        re.match(
            r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*|[IVXLCDM]+)[\.\)]?\s+",
            stripped,
            re.IGNORECASE,
        )
        or re.match(r"^[가나다라마바사아자차카타파하][\.\)]\s+", stripped)
        or re.match(r"^[A-Za-z][\.\)]\s+", stripped)
        or re.match(r"^\(?\d+\)\s+", stripped)
    )


def infer_level_from_title(title: str) -> int | None:
    compact = title.strip()
    unicode_roman_match = re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[\.\-\)]?\s+", compact)
    if unicode_roman_match:
        return 1
    roman_dot_match = re.match(r"^([IVXLCDM]+(?:\.[IVXLCDM]+)+)\b", compact, re.IGNORECASE)
    if roman_dot_match:
        return min(roman_dot_match.group(1).count(".") + 1, 6)
    roman_single_match = re.match(r"^[IVXLCDM]+\b[\.\-\)]?\s+", compact, re.IGNORECASE)
    if roman_single_match:
        return 1
    dot_match = re.match(r"^(\d+(?:\.\d+)+)", compact)
    if dot_match:
        return min(dot_match.group(1).count(".") + 1, 6)
    chapter_match = re.match(r"^제\s*\d+\s*(장|절|항)\b", compact)
    if chapter_match:
        unit = chapter_match.group(1)
        return 1 if unit == "장" else 2 if unit == "절" else 3
    num_match = re.match(r"^\d+[\.\-\)]?\s+", compact)
    if num_match:
        return 1
    return None


def prefix_kind_from_title(title: str) -> str:
    compact = title.strip()
    if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[\.\-\)]?\s+", compact):
        return "roman_single"
    if re.match(r"^[IVXLCDM]+\b[\.\-\)]?\s+", compact, re.IGNORECASE):
        return "roman_single"
    if re.match(r"^([IVXLCDM]+(?:\.[IVXLCDM]+)+)\b", compact, re.IGNORECASE):
        return "roman_dot"
    if re.match(r"^\d+(?:\.\d+)+", compact):
        return "arabic_dot"
    if re.match(r"^\d+[\.\-\)]?\s+", compact):
        return "arabic_single"
    if re.match(r"^제\s*\d+\s*(장|절|항)\b", compact):
        return "chapter"
    if re.match(r"^[A-Za-z][\.\-\)]\s+", compact):
        return "alpha_single"
    return "other"


def _is_bullet_like_title(title: str) -> bool:
    compact = normalize_text(title)
    if not compact:
        return False
    return bool(
        re.match(r"^[가나다라마바사아자차카타파하][\.\)]\s+", compact)
        or re.match(r"^[A-Za-z][\.\)]\s+", compact)
        or re.match(r"^\(?\d+\)\s+", compact)
    )


def _sequence_family(title: str) -> str | None:
    compact = normalize_text(title)
    if not compact:
        return None
    if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[\.\-\)]?\s+", compact):
        return "roman"
    if re.match(r"^[IVXLCDM]+\b[\.\-\)]?\s+", compact, re.IGNORECASE):
        return "roman"
    if re.match(r"^제\s*\d+\s*(장|절|항)\b", compact):
        return "roman"
    if re.match(r"^\d+(?:\.\d+)*[\.\-]?\s+", compact) and not re.match(r"^\(?\d+\)\s+", compact):
        return "arabic"
    if re.match(r"^[가나다라마바사아자차카타파하][\.\)]\s+", compact):
        return "hangul"
    if re.match(r"^[A-Za-z][\.\)]\s+", compact):
        return "latin"
    if re.match(r"^\(?\d+\)\s+", compact):
        return "paren_number"
    if re.match(r"^\(?[A-Za-z]\)\s+", compact):
        return "paren_latin"
    return "other"


def relevel_items(items: list[TocItem]) -> list[TocItem]:
    if not items:
        return items
    normalized: list[TocItem] = []
    for item in items:
        level = item.level
        if not isinstance(level, int) or level < 1 or level > 3:
            inferred = infer_level_from_title(item.title)
            level = inferred if inferred is not None else 1
        normalized.append(
            TocItem(
                level=min(max(level, 1), 3),
                title=item.title,
                page_idx=item.page_idx,
                page_estimate=item.page_estimate,
                anchor=item.anchor,
            )
        )
    return normalized


def trim_toc_depth(items: list[TocItem], max_level: int = 3) -> list[TocItem]:
    return [item for item in items if item.level <= max_level]


def normalize_title_for_match(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip().lower()
    compact = re.sub(r"^[ivxlcdm]+\b[\.\-\)]?\s+", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"^\d+(?:\.\d+)*[\.\-\)]?\s+", "", compact)
    return re.sub(r"^제\s*\d+\s*(장|절|항)\s*", "", compact)


def extract_leading_numbering(title: str) -> str:
    compact = normalize_text(title)
    if not compact:
        return ""
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇\(\)\[\]]+\s*", "", compact)
    match = re.match(
        r"^(제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*)(?:[\.\-\)]\s*|\s+)", compact, flags=re.IGNORECASE
    )
    if not match:
        return ""
    return normalize_text(match.group(1))


def parse_decimal_numbering_prefix(title: str) -> tuple[int, ...] | None:
    prefix = extract_leading_numbering(title)
    if not prefix or not re.fullmatch(r"\d+(?:\.\d+)*", prefix):
        return None
    try:
        return tuple(int(part) for part in prefix.split("."))
    except ValueError:
        return None


def find_missing_numbering_sequences(items: list[TocItem]) -> list[str]:
    if len(items) < 2:
        return []
    grouped: dict[tuple[int, ...], list[tuple[int, ...]]] = {}
    for item in items:
        parsed = parse_decimal_numbering_prefix(item.title)
        if not parsed:
            continue
        parent = parsed[:-1]
        grouped.setdefault(parent, []).append(parsed)
    gaps: list[str] = []
    for parent, values in grouped.items():
        ordered_unique = sorted(set(values))
        prev_last: int | None = None
        for current in ordered_unique:
            current_last = current[-1]
            if prev_last is not None and current_last - prev_last > 1:
                missing = []
                for num in range(prev_last + 1, current_last):
                    candidate = parent + (num,)
                    missing.append(".".join(str(part) for part in candidate))
                if missing:
                    gaps.append(", ".join(missing))
            prev_last = current_last
    return gaps


def reconcile_pages_from_candidates(
    items: list[TocItem], candidates: list[TocItem]
) -> list[TocItem]:
    """items의 page_idx/page_estimate 공백을, 제목이 같은 candidates 항목 값으로 채운다."""
    page_map: dict[str, int] = {}
    estimate_map: dict[str, int] = {}
    for item in candidates:
        key = normalize_title_for_match(item.title)
        if not key:
            continue
        candidate_page_idx = item.page_idx if item.page_idx is not None else item.page_estimate
        if candidate_page_idx is not None and key not in page_map:
            page_map[key] = candidate_page_idx
        if item.page_estimate is not None and key not in estimate_map:
            estimate_map[key] = item.page_estimate

    fixed: list[TocItem] = []
    for item in items:
        key = normalize_title_for_match(item.title)
        page_idx = page_map.get(
            key, item.page_idx if item.page_idx is not None else item.page_estimate
        )
        page_estimate = (
            item.page_estimate if item.page_estimate is not None else estimate_map.get(key)
        )
        if page_idx is None and page_estimate is not None:
            page_idx = page_estimate
        fixed.append(
            TocItem(
                level=item.level,
                title=item.title,
                page_idx=page_idx,
                page_estimate=page_estimate,
                anchor=item.anchor,
            )
        )
    return fixed


def dedup_toc_items_by_title(items: list[TocItem]) -> list[TocItem]:
    dedup: list[TocItem] = []
    seen: set[str] = set()
    for item in items:
        if _is_unsequenced_bullet_title(item.title):
            continue
        key = normalize_title_for_match(item.title)
        if not key or key in seen:
            continue
        dedup.append(item)
        seen.add(key)
    return dedup


def slugify(value: str) -> str:
    compact = re.sub(r"\s+", " ", (value or "")).strip()
    if not compact:
        return "section"
    has_bullet_prefix = bool(re.match(r"^[\-\*•·▪■◆▶◦○□◇※]+\s*", compact))
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇※]+\s*", "", compact)
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", compact).strip("-").lower()
    if has_bullet_prefix:
        cleaned = f"bullet-{cleaned}" if cleaned else "bullet"
    return cleaned or "section"
