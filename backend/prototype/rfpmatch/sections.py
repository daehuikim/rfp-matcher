"""TOC → Section → RfpCard — step3/4 핵심. rfpmatch/step456_shared.py 이식.

`_build_sections_from_final_toc`가 이 파일의 핵심: 최종 TOC 각 항목을 본문(body_blocks) 안의
실제 위치와 매칭해 Section으로 잘라낸다. 원본은 매칭 디버그 정보를 st.session_state에 썼는데,
여기서는 그냥 반환값(match_debug)으로 내보낸다 — 필요하면 호출자가 로깅하면 된다.
"""

from __future__ import annotations

import re
from html import escape

from bs4 import BeautifulSoup

from .cards import _clone_card, build_rfp_cards
from .models import RfpCard, Section, TocItem
from .text_utils import (
    _body_text_blocks,
    _extract_block_indentation_px,  # noqa: F401  (re-exported for callers expecting the old surface)
    _is_heading_like_text,
    _normalize_requirement_text,
    _plain_text_from_html_excerpt,
)
from .toc import anchor_from_text as _anchor_from_text
from .toc import text_with_real_linebreaks as _text_with_real_linebreaks
from .toc_normalize import slugify as _slugify


def _normalize_title_for_match(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip().lower()
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇\(\)\[\]]+\s*", "", compact)
    compact = re.sub(r"^[ivxlcdm]+\b[\.\-\)]?\s+", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"^\d+(?:\.\d+)*[\.\-\)]?\s+", "", compact)
    return re.sub(r"^제\s*\d+\s*(장|절|항)\s*", "", compact)


def _normalize_title_for_relaxed_match(value: str) -> str:
    compact = _normalize_title_for_match(value)
    if not compact:
        return ""
    compact = re.sub(r"[\s\-\–\—_·•,，.:;·'\"`/\\()\[\]{}]+", "", compact)
    return re.sub(r"[※#]+", "", compact)


def _split_heading_prefix(value: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", " ", (value or "")).strip()
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇\(\)\[\]]+\s*", "", compact)
    if not compact:
        return "", ""
    match = re.match(
        r"^(?P<prefix>(?:\d+(?:\.\d+)*|[ivxlcdm]+(?:\.[ivxlcdm]+)*|제\s*\d+\s*(?:장|절|항))[\.\-\)]?)\s*(?P<rest>.*)$",
        compact,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", compact
    prefix = re.sub(r"\s+", " ", match.group("prefix")).strip()
    rest = re.sub(r"\s+", " ", match.group("rest")).strip()
    return prefix, rest


def _title_key(value: str) -> str:
    compact = _normalize_title_for_match(value)
    return re.sub(r"[\W_]+", "", compact, flags=re.UNICODE)


def _title_match(expected: str, actual: str) -> bool:
    expected_compact = re.sub(r"\s+", " ", (expected or "")).strip()
    actual_compact = re.sub(r"\s+", " ", (actual or "")).strip()
    if not expected_compact or not actual_compact:
        return False
    if expected_compact.lower() == actual_compact.lower():
        return True

    expected_prefix, expected_rest = _split_heading_prefix(expected_compact)
    actual_prefix, actual_rest = _split_heading_prefix(actual_compact)
    expected_prefix_key = _title_key(expected_prefix) if expected_prefix else ""
    actual_prefix_key = _title_key(actual_prefix) if actual_prefix else ""
    expected_rest_key = _title_key(expected_rest) if expected_rest else ""
    actual_rest_key = _title_key(actual_rest) if actual_rest else ""

    if expected_rest_key:
        if expected_prefix_key and actual_prefix_key and expected_prefix_key != actual_prefix_key:
            return False
        if actual_rest_key == expected_rest_key or actual_rest_key.startswith(expected_rest_key):
            return True
        # Roman chapter titles are often rewritten slightly between TOC and body
        # (for example "Ⅱ 업무 현황" vs "II. 대상업무 현황"). If the prefixes
        # match and one rest phrase clearly contains the other, treat it as the
        # same section so the boundary does not leak into the previous card.
        return bool(
            expected_rest_key
            and actual_rest_key
            and (expected_rest_key in actual_rest_key or actual_rest_key in expected_rest_key)
        )

    if expected_prefix_key:
        return actual_prefix_key == expected_prefix_key

    expected_key = _title_key(expected_compact)
    actual_key = _title_key(actual_compact)
    if not expected_key or not actual_key:
        return False
    if expected_key == actual_key:
        return True
    expected_relaxed = _normalize_title_for_relaxed_match(expected_compact)
    actual_relaxed = _normalize_title_for_relaxed_match(actual_compact)
    if expected_relaxed and actual_relaxed:
        if expected_relaxed == actual_relaxed:
            return True
        if expected_relaxed.startswith(actual_relaxed) or actual_relaxed.startswith(
            expected_relaxed
        ):
            return True
    return actual_key.startswith(expected_key)


def _is_toc_like_body_line(text: str) -> bool:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return False
    if re.search(r"[.\-_·•\s]{2,}\d{1,4}\s*$", compact):
        return True
    if re.match(
        r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+|[가나다라마바사아자차카타파하]\.)\s+",
        compact,
        flags=re.IGNORECASE,
    ):
        return bool(re.search(r"\d{1,4}\s*$", compact))
    return False


def _detect_body_start_index(body_blocks: list[dict], toc_items: list[TocItem]) -> int:
    if not body_blocks:
        return 0

    toc_keys = {_title_key(item.title) for item in toc_items if item.title}
    if not toc_keys:
        return 0

    for idx, block in enumerate(body_blocks):
        text = block["text"]
        tag = block["tag"]
        key = _title_key(text)
        is_heading = tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or _is_heading_like_text(text)
        if (
            not is_heading
            and tag == "li"
            and key in toc_keys
            and len(text) <= 40
            and not _is_toc_like_body_line(text)
        ):
            is_heading = True
        if not is_heading:
            continue
        if key not in toc_keys:
            continue

        lookahead = body_blocks[idx + 1 : idx + 5]
        toc_like_count = sum(
            1 for candidate in lookahead if _is_toc_like_body_line(candidate["text"])
        )
        if toc_like_count >= 2:
            continue
        return idx

    return 0


def _is_citation_like_text(text: str) -> bool:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return False
    return bool(
        re.match(r"^[『「“‘].+[』」”’]$", compact)
        or re.match(r"^['\"].+['\"]$", compact)
        or re.match(
            r"^『[^』]{1,120}』\s*(?:을|를|은|는|이|가|에|로|로서|에서|에 대한|을 위한|을 통해)",
            compact,
        )
        or re.match(
            r"^「[^」]{1,120}」\s*(?:을|를|은|는|이|가|에|로|로서|에서|에 대한|을 위한|을 통해)",
            compact,
        )
        or re.match(
            r"^“[^”]{1,120}”\s*(?:을|를|은|는|이|가|에|로|로서|에서|에 대한|을 위한|을 통해)",
            compact,
        )
        or re.match(
            r"^‘[^’]{1,120}’\s*(?:을|를|은|는|이|가|에|로|로서|에서|에 대한|을 위한|을 통해)",
            compact,
        )
    )


def _block_title_candidates(block: dict) -> list[str]:
    candidates: list[str] = []
    tag = str(block.get("tag") or "").lower()
    text = str(block.get("text") or "").strip()
    if text and (tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or not _is_citation_like_text(text)):
        candidates.append(text)

    html = str(block.get("html") or "").strip()
    if html:
        soup = BeautifulSoup(html, "html.parser")
        raw_lines = (
            _text_with_real_linebreaks(soup).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        )
        for line in raw_lines:
            normalized = re.sub(r"\s+", " ", line).strip()
            if (
                normalized
                and normalized not in candidates
                and (
                    tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
                    or not _is_citation_like_text(normalized)
                )
            ):
                candidates.append(normalized)
        for cell in soup.find_all(["th", "td", "p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
            normalized = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            if (
                normalized
                and normalized not in candidates
                and (
                    cell.name in {"h1", "h2", "h3", "h4", "h5", "h6"}
                    or not _is_citation_like_text(normalized)
                )
            ):
                candidates.append(normalized)
    return candidates


def _is_high_level_section_title(title: str) -> bool:
    compact = re.sub(r"\s+", " ", (title or "")).strip()
    if not compact:
        return False
    return bool(
        re.match(r"^(?:[IVXLCDM]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)[\.\)]?\s*.+", compact, flags=re.IGNORECASE)
        or re.match(r"^제?\s*\d+\s*(?:장|절|항)[\.\)]?\s*.+", compact, flags=re.IGNORECASE)
        or re.match(r"^\d+\.\s*.+", compact)
    )


def _find_title_start_index(body_blocks: list[dict], title: str, start_from: int = 0) -> int | None:
    if not re.sub(r"\s+", "", (title or "")).strip():
        return None
    if not body_blocks:
        return None
    is_high_level_title = _is_high_level_section_title(title)
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        if is_high_level_title and str(block.get("tag") or "").lower() == "table":
            continue
        candidates = _block_title_candidates(block)
        if is_high_level_title:
            block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
            candidates = [block_text] if block_text else []
        for candidate in candidates:
            if _title_match(title, candidate):
                return idx
    if is_high_level_title:
        fallback_idx = max(start_from, 0)
        return fallback_idx if fallback_idx < len(body_blocks) else None
    return None


def _title_candidate_score(block: dict, title: str, candidate: str) -> tuple[int, int]:
    tag = str(block.get("tag") or "").lower()
    text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    candidate_text = re.sub(r"\s+", " ", candidate).strip()
    expected_key = _title_key(title)
    candidate_key = _title_key(candidate_text)
    if not expected_key or not candidate_key:
        return -1, -1
    if not _title_match(title, candidate_text):
        return -1, -1
    heading_bonus = (
        100 if (tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or _is_heading_like_text(text)) else 0
    )
    exact_bonus = (
        50 if _normalize_title_for_match(title) == _normalize_title_for_match(candidate_text) else 0
    )
    prefix_bonus = 20 if candidate_key.startswith(expected_key) else 0
    length_bonus = min(len(candidate_key), len(expected_key))
    return heading_bonus + exact_bonus + prefix_bonus + length_bonus, len(candidate_key)


def _find_title_start_index_with_page(
    body_blocks: list[dict],
    title: str,
    start_from: int = 0,
    page_idx: int | None = None,
) -> int | None:
    if not body_blocks:
        return None
    best_idx: int | None = None
    best_score: tuple[int, int] = (-1, -1)
    is_high_level_title = _is_high_level_section_title(title)
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        if is_high_level_title and str(block.get("tag") or "").lower() == "table":
            continue
        block_page_idx = block.get("page_idx")
        if page_idx is not None and block_page_idx is not None and block_page_idx != page_idx:
            continue
        candidates = _block_title_candidates(block)
        if is_high_level_title:
            block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
            candidates = [block_text] if block_text else []
        for candidate in candidates:
            if is_high_level_title and str(block.get("tag") or "").lower() not in {
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "p",
                "li",
                "div",
                "section",
                "article",
                "figcaption",
            }:
                continue
            score = _title_candidate_score(block, title, candidate)
            if score > best_score:
                best_idx = idx
                best_score = score
    if best_idx is not None:
        return best_idx
    if is_high_level_title:
        fallback_idx = max(start_from, 0)
        return fallback_idx if fallback_idx < len(body_blocks) else None
    return _find_title_start_index(body_blocks, title, start_from)


def _anchor_match(anchor: str, candidate: str) -> bool:
    anchor_text = _normalize_requirement_text(anchor)
    candidate_text = _normalize_requirement_text(candidate)
    if not anchor_text or not candidate_text:
        return False
    anchor_slug = _anchor_from_text(anchor_text)
    candidate_slug = _anchor_from_text(candidate_text)
    if anchor_slug and candidate_slug:
        return anchor_slug.lower() == candidate_slug.lower()
    return anchor_text.lower() == candidate_text.lower()


def _anchor_candidate_score(block: dict, anchor: str, candidate: str) -> tuple[int, int]:
    anchor_text = _normalize_requirement_text(anchor)
    candidate_text = _normalize_requirement_text(candidate)
    if not anchor_text or not candidate_text:
        return -1, -1
    if not _anchor_match(anchor_text, candidate_text):
        return -1, -1

    anchor_slug = _anchor_from_text(anchor_text).lower()
    candidate_slug = _anchor_from_text(candidate_text).lower()
    anchor_relaxed = _normalize_title_for_relaxed_match(anchor_text)
    candidate_relaxed = _normalize_title_for_relaxed_match(candidate_text)
    tag = str(block.get("tag") or "").lower()
    text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    heading_bonus = (
        20 if (tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or _is_heading_like_text(text)) else 0
    )

    if anchor_slug and candidate_slug and anchor_slug == candidate_slug:
        return 300 + heading_bonus, len(candidate_slug)
    if anchor_text.lower() == candidate_text.lower():
        return 250 + heading_bonus, len(candidate_text)
    if anchor_relaxed and candidate_relaxed and anchor_relaxed == candidate_relaxed:
        return 220 + heading_bonus, len(candidate_relaxed)
    return 120 + heading_bonus, len(candidate_text)


def _find_anchor_start_index_with_page(
    body_blocks: list[dict],
    anchor: str,
    start_from: int = 0,
    page_idx: int | None = None,
) -> int | None:
    compact_anchor = re.sub(r"\s+", " ", (anchor or "")).strip()
    if not compact_anchor:
        return None
    if not body_blocks:
        return None
    best_idx: int | None = None
    best_score: tuple[int, int] = (-1, -1)
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        block_page_idx = block.get("page_idx")
        if page_idx is not None and block_page_idx is not None and block_page_idx != page_idx:
            continue
        candidates = list(_block_title_candidates(block))
        raw_text = _normalize_requirement_text(str(block.get("text") or ""))
        if raw_text:
            candidates.append(raw_text)
        html = str(block.get("html") or "").strip()
        if html:
            soup = BeautifulSoup(html, "html.parser")
            flattened_html_text = _normalize_requirement_text(soup.get_text(" ", strip=True))
            if flattened_html_text:
                candidates.append(flattened_html_text)
            for tag in soup.find_all(True):
                raw_id = tag.get("id")
                if raw_id:
                    candidates.append(str(raw_id).strip())
                for attr_name in ("data-anchor", "data-title", "name"):
                    raw_attr = tag.get(attr_name)
                    if raw_attr:
                        candidates.append(str(raw_attr).strip())
        for candidate in candidates:
            if not candidate:
                continue
            if _anchor_match(compact_anchor, candidate):
                score = _anchor_candidate_score(block, compact_anchor, candidate)
                if score > best_score:
                    best_idx = idx
                    best_score = score
    if best_idx is not None:
        return best_idx
    return None


def _debug_title_match_candidates(
    body_blocks: list[dict],
    title: str,
    start_from: int = 0,
    context_size: int = 2,
    *,
    match_query: str | None = None,
) -> dict:
    expected = _normalize_title_for_match(title)
    query = (match_query or title or "").strip()
    expected_key = _title_key(query)
    expected_prefix, expected_rest = _split_heading_prefix(query)
    is_high_level_title = _is_high_level_section_title(query or title)
    use_query_only = bool(
        match_query and _normalize_title_for_match(match_query) != _normalize_title_for_match(title)
    )
    debug: dict = {
        "title": title,
        "match_query": query,
        "match_mode": "anchor" if use_query_only else "title",
        "start_from": start_from,
        "expected": expected,
        "expected_key": expected_key,
        "expected_prefix": expected_prefix,
        "expected_rest": expected_rest,
        "matched": False,
        "matched_index": None,
        "matched_candidate": "",
        "matched_block_text": "",
        "matched_text": "",
        "matched_tag": "",
        "reason": "",
        "nearby_blocks": [],
        "all_candidate_hits": [],
    }
    if not expected_key:
        debug["reason"] = "empty_expected_key"
        return debug

    best_match_index: int | None = None
    best_match_reason = ""
    best_match_candidate = ""
    for idx in range(max(start_from, 0), len(body_blocks)):
        block = body_blocks[idx]
        if is_high_level_title and str(block.get("tag") or "").lower() == "table":
            continue
        candidates = _block_title_candidates(block)
        raw_text = _normalize_requirement_text(str(block.get("text") or ""))
        if raw_text:
            candidates.append(raw_text)
        if is_high_level_title and not use_query_only:
            block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
            candidates = [block_text] if block_text else []
        elif not use_query_only:
            html = str(block.get("html") or "").strip()
            if html:
                candidates.append(
                    _normalize_requirement_text(
                        BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
                    )
                )
        elif use_query_only:
            html = str(block.get("html") or "").strip()
            if html:
                soup = BeautifulSoup(html, "html.parser")
                flattened_html_text = _normalize_requirement_text(soup.get_text(" ", strip=True))
                if flattened_html_text:
                    candidates.append(flattened_html_text)
                for tag in soup.find_all(True):
                    raw_id = tag.get("id")
                    if raw_id:
                        candidates.append(str(raw_id).strip())
                    for attr_name in ("data-anchor", "data-title", "name"):
                        raw_attr = tag.get(attr_name)
                        if raw_attr:
                            candidates.append(str(raw_attr).strip())
        for candidate in candidates:
            if use_query_only:
                if not _anchor_match(query, candidate):
                    continue
            elif not _title_match(title, candidate):
                continue
            if use_query_only or _anchor_match(query, candidate):
                best_match_index = idx
                best_match_candidate = candidate
                actual_prefix, actual_rest = _split_heading_prefix(candidate)
                if expected_rest and expected_prefix:
                    best_match_reason = "prefix+rest" if actual_rest else "prefix"
                elif expected_prefix:
                    best_match_reason = "prefix"
                else:
                    best_match_reason = "exact"
                break
        if best_match_index is not None:
            break

    if best_match_index is not None:
        block = body_blocks[best_match_index]
        debug["matched"] = True
        debug["matched_index"] = best_match_index
        debug["matched_candidate"] = best_match_candidate
        debug["matched_block_text"] = block.get("text", "")
        debug["matched_text"] = block.get("text", "")
        debug["matched_tag"] = block.get("tag", "")
        debug["reason"] = best_match_reason
    elif is_high_level_title and not use_query_only and 0 <= start_from < len(body_blocks):
        block = body_blocks[start_from]
        debug["matched"] = True
        debug["matched_index"] = start_from
        debug["matched_candidate"] = title
        debug["matched_block_text"] = block.get("text", "")
        debug["matched_text"] = block.get("text", "")
        debug["matched_tag"] = block.get("tag", "")
        debug["reason"] = "high_level_fallback"
    else:
        debug["reason"] = "not_found_after_cursor"

    for idx, block in enumerate(body_blocks):
        if is_high_level_title and str(block.get("tag") or "").lower() == "table":
            continue
        candidates = _block_title_candidates(block)
        if is_high_level_title and not use_query_only:
            block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
            candidates = [block_text] if block_text else []
        elif not use_query_only:
            html = str(block.get("html") or "").strip()
            if html:
                candidates.append(
                    _normalize_requirement_text(
                        BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
                    )
                )
        elif use_query_only:
            html = str(block.get("html") or "").strip()
            if html:
                soup = BeautifulSoup(html, "html.parser")
                flattened_html_text = _normalize_requirement_text(soup.get_text(" ", strip=True))
                if flattened_html_text:
                    candidates.append(flattened_html_text)
                for tag in soup.find_all(True):
                    raw_id = tag.get("id")
                    if raw_id:
                        candidates.append(str(raw_id).strip())
                    for attr_name in ("data-anchor", "data-title", "name"):
                        raw_attr = tag.get(attr_name)
                        if raw_attr:
                            candidates.append(str(raw_attr).strip())
        for candidate in candidates:
            if use_query_only:
                if not _anchor_match(query, candidate):
                    continue
            elif not _title_match(title, candidate):
                continue
            debug["all_candidate_hits"].append(
                {
                    "index": idx,
                    "tag": block.get("tag", ""),
                    "text": candidate,
                    "key": _title_key(candidate),
                }
            )
            break

    if best_match_index is None:
        lower = max(start_from - context_size, 0)
        upper = min(len(body_blocks), start_from + context_size + 1)
    else:
        lower = max(best_match_index - context_size, 0)
        upper = min(len(body_blocks), best_match_index + context_size + 1)
    debug["nearby_blocks"] = [
        {
            "index": idx,
            "tag": body_blocks[idx].get("tag", ""),
            "text": body_blocks[idx].get("text", ""),
        }
        for idx in range(lower, upper)
    ]

    return debug


def _split_leading_title_block(block: dict, title: str) -> list[dict]:
    block_text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    title_text = re.sub(r"\s+", " ", (title or "")).strip()
    if not block_text or not title_text:
        return [block]
    if block_text.lower() == title_text.lower():
        return [block]
    if not block_text.lower().startswith(title_text.lower()):
        return [block]

    remainder = block_text[len(title_text) :].strip()
    if not remainder:
        return [block]

    tag = str(block.get("tag") or "p").strip().lower() or "p"
    title_html = (
        f"<{tag}><p>{escape(title_text)}</p></{tag}>"
        if tag == "li"
        else f"<{tag}>{escape(title_text)}</{tag}>"
    )
    remainder_html = f"<p>{escape(remainder)}</p>"
    return [
        {"html": title_html, "text": title_text, "tag": tag},
        {"html": remainder_html, "text": remainder, "tag": "p"},
    ]


def build_sections_from_final_toc(
    html: str, toc_items: list[TocItem]
) -> tuple[list[Section], list[dict]]:
    """최종 TOC → 본문 위치 매칭 → Section 리스트. (sections, match_debug) 반환.

    match_debug는 각 TOC 항목이 본문 어디에 매칭됐는지(혹은 왜 실패했는지) 기록한 진단 정보 —
    원본은 이걸 Streamlit session_state에 썼지만 여기서는 그냥 반환값으로 내보낸다.
    """
    soup = BeautifulSoup(html, "html.parser")
    # 일부 문서(JB 계열처럼)에서는 본문 시작점 분리가 너무 뒤로 밀려
    # 실제 섹션 제목이 body_region 밖(pre 쪽)에 남는 경우가 있다.
    # 이런 경우까지 놓치지 않도록 전체 body 기준 블록을 우선 사용한다.
    body_blocks = _body_text_blocks(str(soup.body or soup))
    body_start_idx = _detect_body_start_index(body_blocks, toc_items)
    if body_start_idx > 0:
        body_blocks = body_blocks[body_start_idx:]
    matched_positions: list[tuple[int, int, TocItem]] = []
    match_debug: list[dict] = []
    candidate_cursor = 0
    for toc_idx, item in enumerate(toc_items):
        match_key = _normalize_requirement_text(str(item.anchor or "")).strip() or item.title
        debug_entry = _debug_title_match_candidates(
            body_blocks, item.title, candidate_cursor, match_query=match_key
        )
        debug_entry["toc_index"] = toc_idx
        debug_entry["level"] = item.level
        debug_entry["page_idx"] = item.page_idx
        debug_entry["match_key"] = match_key
        debug_entry["match_anchor"] = item.anchor
        start_idx = None
        if item.anchor and _normalize_requirement_text(str(item.anchor)):
            start_idx = _find_anchor_start_index_with_page(
                body_blocks, item.anchor, candidate_cursor, item.page_idx
            )
            if start_idx is not None:
                debug_entry["matched_by"] = "anchor"
            elif candidate_cursor > 0:
                start_idx = _find_anchor_start_index_with_page(
                    body_blocks, item.anchor, 0, item.page_idx
                )
                if start_idx is not None:
                    debug_entry["matched_by"] = "anchor_global"
            # LLM이 매긴 page_idx는 근사치일 수 있다(자동화로 사람의 페이지 보정 단계가
            # 빠졌으므로 원본보다 더 자주 어긋난다). page 제약 때문에 완전히 못 찾은
            # 경우, 마지막으로 page 제약을 풀고 앵커/텍스트만으로 재시도한다.
            if start_idx is None and item.page_idx is not None:
                start_idx = _find_anchor_start_index_with_page(
                    body_blocks, item.anchor, candidate_cursor, None
                )
                if start_idx is None and candidate_cursor > 0:
                    start_idx = _find_anchor_start_index_with_page(
                        body_blocks, item.anchor, 0, None
                    )
                if start_idx is not None:
                    debug_entry["matched_by"] = "anchor_no_page"
        if start_idx is None and not item.anchor:
            start_idx = _find_title_start_index_with_page(
                body_blocks, item.title, candidate_cursor, item.page_idx
            )
            if start_idx is not None and not debug_entry.get("matched_by"):
                debug_entry["matched_by"] = "title"
        if start_idx is None and item.anchor:
            debug_entry["reason"] = "anchor_not_found"
            debug_entry["matched_by"] = "anchor"
        elif start_idx is None:
            debug_entry["reason"] = "title_not_found"
        debug_entry["resolved_index"] = start_idx
        if start_idx is not None:
            matched_block = body_blocks[start_idx]
            debug_entry["matched_page_idx"] = matched_block.get("page_idx")
        match_debug.append(debug_entry)
        if start_idx is None:
            continue
        matched_positions.append((start_idx, toc_idx, item))
        candidate_cursor = start_idx + 1

    matched_positions.sort(key=lambda entry: (entry[0], entry[1]))
    span_end_by_start: dict[int, int] = {}
    for idx, (start_idx, _, _) in enumerate(matched_positions):
        next_start = (
            matched_positions[idx + 1][0] if idx + 1 < len(matched_positions) else len(body_blocks)
        )
        span_end_by_start[start_idx] = next_start

    matched_titles_by_toc_index = {
        toc_idx: (start_idx, item) for start_idx, toc_idx, item in matched_positions
    }
    ordered: list[Section] = []
    for toc_idx, item in enumerate(toc_items):
        matched = matched_titles_by_toc_index.get(toc_idx)
        if matched:
            start_idx, _ = matched
            end_idx = span_end_by_start.get(start_idx, len(body_blocks))
            section_blocks = list(body_blocks[start_idx:end_idx])
            if section_blocks and (
                not item.anchor
                or _normalize_title_for_match(item.anchor) == _normalize_title_for_match(item.title)
            ):
                section_blocks = (
                    _split_leading_title_block(section_blocks[0], item.title) + section_blocks[1:]
                )
            html_source = "\n".join(block["html"] for block in section_blocks).strip()
            text_source = " ".join(block["text"] for block in section_blocks).strip()
            ordered.append(
                Section(
                    title=item.title,
                    anchor=item.anchor
                    or _slugify(
                        item.title
                    ),  # 원본 버그 수정: 미정의 _slugify → toc_normalize.slugify
                    level=item.level,
                    page_idx=item.page_idx,
                    html=html_source,
                    text=text_source,
                )
            )
            continue
        ordered.append(
            Section(
                title=item.title,
                anchor=item.anchor,
                level=item.level,
                page_idx=item.page_idx,
                html="",
                text="",
            )
        )
    return ordered, match_debug


def build_cards_from_sections(sections: list[Section]) -> list[RfpCard]:
    cards = build_rfp_cards(sections)
    return _repair_misattached_leading_tables(cards)


def _card_contains_table(card: RfpCard) -> bool:
    return "<table" in str(getattr(card, "html_excerpt", "") or "").lower()


def _card_plain_lines(card: RfpCard) -> list[str]:
    return [
        line.strip()
        for line in _plain_text_from_html_excerpt(
            str(getattr(card, "html_excerpt", "") or "")
        ).splitlines()
        if line.strip()
    ]


def _looks_like_table_intro_card(card: RfpCard) -> bool:
    if _card_contains_table(card):
        return False
    lines = _card_plain_lines(card)
    if len(lines) < 2:
        return False
    body_lines = lines[1:]
    joined = " ".join(body_lines)
    intro_markers = (
        "다음과 같습니다",
        "다음과 같",
        "아래와 같습니다",
        "아래와 같",
        "다음 서류",
        "다음 표",
        "다음과 같은",
    )
    return any(marker in joined for marker in intro_markers)


def _split_card_heading_table_tail(card: RfpCard) -> tuple[list[dict], list[dict], list[dict]]:
    blocks = _body_text_blocks(str(getattr(card, "html_excerpt", "") or ""))
    if not blocks:
        return [], [], []

    leading_heading: list[dict] = []
    leading_tables: list[dict] = []
    idx = 0
    while idx < len(blocks) and blocks[idx].get("tag") != "table":
        leading_heading.append(blocks[idx])
        idx += 1
    while idx < len(blocks) and blocks[idx].get("tag") == "table":
        leading_tables.append(blocks[idx])
        idx += 1
    trailing_blocks = blocks[idx:]
    return leading_heading, leading_tables, trailing_blocks


def _repair_misattached_leading_tables(cards: list[RfpCard]) -> list[RfpCard]:
    if not cards:
        return cards

    repaired = list(cards)
    for idx, card in enumerate(repaired):
        if not _looks_like_table_intro_card(card):
            continue

        for look_ahead in range(idx + 1, min(idx + 4, len(repaired))):
            candidate = repaired[look_ahead]
            if str(getattr(candidate, "section", "") or "") != str(
                getattr(card, "section", "") or ""
            ):
                break
            if not _card_contains_table(candidate):
                continue

            leading_heading, leading_tables, trailing_blocks = _split_card_heading_table_tail(
                candidate
            )
            if not leading_tables:
                continue
            if not leading_heading:
                continue

            # Only repair when the later card starts with its own heading and
            # the table is placed before the card's actual body content.
            if not trailing_blocks:
                continue

            repaired[idx] = _clone_card(
                card,
                subject=getattr(card, "subject", None) or card.requirement,
                card_no=getattr(card, "card_no", None) or str(card.card_id),
            )
            repaired[idx].html_excerpt = (
                str(getattr(card, "html_excerpt", "") or "").strip()
                + "\n"
                + "\n".join(block["html"] for block in leading_tables).strip()
            ).strip()

            repaired[look_ahead] = _clone_card(
                candidate,
                subject=getattr(candidate, "subject", None) or candidate.requirement,
                card_no=getattr(candidate, "card_no", None) or str(candidate.card_id),
            )
            repaired[look_ahead].html_excerpt = "\n".join(
                block["html"] for block in [*leading_heading, *trailing_blocks]
            ).strip()
            break
    return repaired
