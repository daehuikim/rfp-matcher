"""LLM 기반 TOC(목차) 재구성. rfpmatch/shared_llm.py 이식.

원본은 raw OpenAI Responses API + 마크다운 펜스 걷어내는 수동 JSON 파싱을 썼지만,
여기서는 저장소 관례(app.llm.factory.build_llm_client + structured_output)로 교체해
- app_state.load_openai_api_key() 의존 제거(headless 실행 가능)
- LLM_PROVIDER=fake로 유닛테스트 가능
- 토큰가격표 중복 정의 제거(app/llm/usage.py가 이미 전담)
를 얻는다. TOC 자동복구(번호 공백 감지 시 해당 구간만 1회 추가질의)는 원본 로직 그대로
유지 — 이것이 계획서의 "TOC 자동복구" 자동화에 해당한다(사람 개입 없이 파이프라인 안에서 처리).
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from .models import TocItem
from .toc import detect_txt_toc_style
from .toc_normalize import (
    dedup_toc_items_by_title,
    extract_leading_numbering,
    find_missing_numbering_sequences,
    rows_to_toc_items,
    slugify,
)


class _TocEntry(BaseModel):
    level: int
    title: str
    page: int | None = None


class _TocRows(BaseModel):
    rows: list[_TocEntry]


def _preprocess_txt_for_toc_llm(raw_text: str) -> str:
    text = raw_text or ""
    if not text.strip():
        return text

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    normalized_lines: list[str] = []
    for line in text.split("\n"):
        compact = line.strip()
        if not compact:
            normalized_lines.append("")
            continue

        # Word/markdown export noise that destabilizes TOC reconstruction.
        compact = compact.replace("오류! 책갈피가 정의되어 있지 않습니다.", "").strip()
        if not compact:
            continue

        # Preserve heading text but drop markdown heading fences.
        compact = re.sub(r"^\#{1,6}\s*", "", compact).strip()

        # Split cases like:
        # "- 2. ICT 요청사항 .... 11 2.1. 당사 시스템 구성도 .... 11"
        compact = re.sub(
            r"(?<=\d)\s+(?=(?:\d+(?:\.\d+)+\.?\s+|[IVXLCDM]+[\.\)]\s+|제\s*\d+\s*(?:장|절|항)\s+))",
            "\n",
            compact,
            flags=re.IGNORECASE,
        )

        # Split another TOC item that was concatenated after a page number:
        # "...54 - 6. AI 리터러시 요청사항 ..."
        compact = re.sub(
            r"(?<=\d)\s*-\s+(?=(?:\d+(?:\.\d+)*\.?\s+|[IVXLCDM]+[\.\)]\s+))",
            "\n- ",
            compact,
            flags=re.IGNORECASE,
        )

        normalized_lines.extend(part.strip() for part in compact.split("\n") if part.strip())

    cleaned = "\n".join(normalized_lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _strip_leading_toc_region_from_txt(raw_text: str) -> str:
    text = raw_text or ""
    if not text.strip():
        return text

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    toc_started = False
    toc_end_idx: int | None = None

    def _looks_like_toc_heading(line: str) -> bool:
        compact = re.sub(r"\s+", " ", line).strip().lower()
        return compact in {"목차", "목 차", "contents", "table of contents"}

    def _looks_like_body_heading(line: str) -> bool:
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact:
            return False
        return bool(
            re.match(
                r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)+|[IVXLCDM]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)[\.\)]?\s+.+",
                compact,
                re.IGNORECASE,
            )
            or re.match(r"^[가나다라마바사아자차카타파하]\.\s+.+", compact)
        )

    for idx, line in enumerate(lines):
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact:
            continue
        if not toc_started and _looks_like_toc_heading(compact):
            toc_started = True
            continue
        if toc_started and _looks_like_body_heading(compact):
            toc_end_idx = idx
            break

    if toc_end_idx is None:
        return text
    return "\n".join(lines[toc_end_idx:]).strip()


def _infer_missing_pages_from_body(items: list[TocItem], raw_text: str) -> list[TocItem]:
    if not raw_text:
        return items

    # 1. Form Feed (\f) 문자가 존재하는 경우 -> 가장 정확함
    pages_by_ff = raw_text.split("\f")
    if len(pages_by_ff) > 1:
        for item in items:
            if item.page_idx is not None and item.page_idx > 0:
                continue
            normalized_title = re.sub(r"\s+", "", item.title)
            if not normalized_title:
                continue
            for p_idx, page_content in enumerate(pages_by_ff, start=1):
                normalized_content = re.sub(r"\s+", "", page_content)
                if normalized_title in normalized_content:
                    item.page_estimate = p_idx
                    break
        return items

    def _extract_page_marker_from_text(text: str) -> int | None:
        compact = re.sub(r"\s+", " ", (text or "")).strip()
        if not compact:
            return None
        marker_patterns = (
            re.compile(r"^\s*\[?\s*p\.?\s*(?P<page>\d{1,4})\s*\]?\s*$", re.IGNORECASE),
            re.compile(r"^\s*p\.?\s*(?P<page>\d{1,4})\s*$", re.IGNORECASE),
            re.compile(r"^\s*page\s*[:.]?\s*(?P<page>\d{1,4})\s*$", re.IGNORECASE),
            re.compile(r"^\s*(?P<page>\d{1,4})\s*쪽\s*$", re.IGNORECASE),
            re.compile(r"^\s*(?P<page>\d{1,4})\s*페이지\s*$", re.IGNORECASE),
            re.compile(r"^\s*\[?p\.(?P<page>\d{1,4})\]?\s*$", re.IGNORECASE),
            re.compile(
                r"^\s*\[?\s*(?P<page>\d{1,4})\s*[-–—/]\s*(?:\d{1,4})\s*\]?\s*$", re.IGNORECASE
            ),
            re.compile(r"^\s*[-–—]\s*(?P<page>\d{1,4})\s*[-–—]?\s*$", re.IGNORECASE),
            re.compile(r"^\s*(?P<page>\d{1,4})\s*[-–—]\s*$", re.IGNORECASE),
            re.compile(r"^\s*(?P<page>\d{1,4})\s*/\s*\d{1,4}\s*$", re.IGNORECASE),
        )
        for pattern in marker_patterns:
            match = pattern.search(compact)
            if match:
                page = int(match.group("page"))
                return page if page > 0 else None
        return None

    def _normalize_for_match(value: str) -> str:
        compact = re.sub(r"\s+", " ", (value or "")).strip().lower()
        compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇※\(\)\[\]]+\s*", "", compact)
        compact = re.sub(r"^[ivxlcdm]+\b[\.\-\)]?\s+", "", compact, flags=re.IGNORECASE)
        compact = re.sub(r"^\d+(?:\.\d+)*[\.\-\)]?\s*", "", compact)
        compact = re.sub(r"^제\s*\d+\s*(장|절|항)\s*", "", compact)
        return re.sub(r"[.\-_·•\s]{2,}\d{1,4}\s*$", "", compact)

    lines = raw_text.split("\n")
    if not lines:
        return items

    unassigned_items = [item for item in items if item.page_idx is None or item.page_idx <= 0]
    if not unassigned_items:
        return items

    normalized_lines = [re.sub(r"\s+", "", line) for line in lines]

    def _candidate_title_keys(title: str) -> list[str]:
        compact = re.sub(r"\s+", "", title)
        if not compact:
            return []
        stripped = re.sub(r"^[\-\*•·▪■◆▶◦○□◇\(\)\[\]]+", "", compact)
        numbered_stripped = re.sub(
            r"^(?:제?\d+(?:\.\d+)*|[IVXLCDM]+|[가나다라마바사아자차카타파하]|[A-Za-z])[\.\)]?",
            "",
            stripped,
            flags=re.IGNORECASE,
        )
        keys = [compact, _normalize_for_match(title).replace(" ", "")]
        for candidate in (stripped, numbered_stripped):
            if candidate and candidate not in keys:
                keys.append(candidate)
        return keys

    def _find_nearby_page_idx(start_idx: int, lookahead: int = 12) -> int | None:
        current_marker = _extract_page_marker_from_text(lines[start_idx])
        if current_marker is not None:
            return current_marker
        # 제목 뒤에 나오는 첫 페이지 표식을 우선 사용한다.
        # 가까운 범위에 없더라도 뒤쪽 전체를 훑어서 가장 먼저 나오는 표식을 찾는다.
        end_idx = len(lines)
        for idx in range(start_idx + 1, end_idx):
            marker_idx = _extract_page_marker_from_text(lines[idx])
            if marker_idx is not None:
                return marker_idx
        return None

    for item in unassigned_items:
        found_idx: int | None = None
        for idx, norm_line in enumerate(normalized_lines):
            for key in _candidate_title_keys(item.title):
                if key and (key in norm_line or norm_line in key):
                    found_idx = idx
                    break
            if found_idx is not None:
                break
        if found_idx is None:
            continue
        page_idx = _find_nearby_page_idx(found_idx)
        if page_idx is not None:
            item.page_estimate = page_idx

    return items


def _promote_page_estimates(items: list[TocItem]) -> list[TocItem]:
    promoted: list[TocItem] = []
    for item in items:
        page_idx = item.page_idx if item.page_idx is not None else item.page_estimate
        promoted.append(
            TocItem(
                level=item.level,
                title=item.title,
                page_idx=page_idx,
                page_estimate=item.page_estimate,
                anchor=item.anchor,
            )
        )
    return promoted


def _normalize_txt_pages_to_estimates(items: list[TocItem]) -> list[TocItem]:
    normalized: list[TocItem] = []
    for item in items:
        normalized.append(
            TocItem(
                level=item.level,
                title=item.title,
                page_idx=None,
                page_estimate=item.page_idx if item.page_idx is not None else item.page_estimate,
                anchor=item.anchor,
            )
        )
    return normalized


def _drop_unsequenced_bullet_items(items: list[TocItem]) -> list[TocItem]:
    filtered: list[TocItem] = []
    for item in items:
        compact = re.sub(r"\s+", " ", (item.title or "")).strip()
        if not compact:
            continue
        if re.match(r"^[\-\*•·▪■◆▶◦○□◇※]", compact):
            stripped = re.sub(r"^[\-\*•·▪■◆▶◦○□◇※]+\s*", "", compact)
            if not bool(
                re.match(
                    r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*|[IVXLCDM]+)[\.\)]?\s+",
                    stripped,
                    re.IGNORECASE,
                )
                or re.match(r"^[가나다라마바사아자차카타파하][\.\)]\s+", stripped)
                or re.match(r"^[A-Za-z][\.\)]\s+", stripped)
                or re.match(r"^\(?\d+\)\s+", stripped)
            ):
                continue
        filtered.append(item)
    return filtered


_ROMAN_PREFIX_TRANSLATION = str.maketrans(
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
    }
)


def _heading_prefix_key(title: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", " ", (title or "")).strip()
    if not compact:
        return "", ""
    match = re.match(
        r"^(?P<prefix>(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*|[IVXLCDM]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)[\.\)]?)\s+(?P<rest>.+)$",
        compact,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", ""
    prefix = re.sub(r"\s+", "", match.group("prefix") or "")
    prefix = re.sub(r"[\.\)\-]+$", "", prefix)
    prefix = prefix.translate(_ROMAN_PREFIX_TRANSLATION).upper()
    rest = re.sub(r"\s+", "", match.group("rest") or "").lower()
    return prefix, rest


def _reconcile_titles_from_raw_text(items: list[TocItem], raw_text: str) -> list[TocItem]:
    if not items or not raw_text.strip():
        return items

    prefix_candidates: dict[str, list[str]] = {}
    for line in raw_text.splitlines():
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact:
            continue
        compact = re.sub(r"[.\-_·•\s]{2,}\d{1,4}\s*$", "", compact).strip()
        prefix_key, rest_key = _heading_prefix_key(compact)
        if not prefix_key or not rest_key:
            continue
        prefix_candidates.setdefault(prefix_key, [])
        if compact not in prefix_candidates[prefix_key]:
            prefix_candidates[prefix_key].append(compact)

    reconciled: list[TocItem] = []
    for item in items:
        current_prefix, current_rest = _heading_prefix_key(item.title)
        if not current_prefix:
            reconciled.append(item)
            continue
        best_title = item.title
        best_score = len(re.sub(r"\s+", "", best_title))
        for candidate in prefix_candidates.get(current_prefix, []):
            candidate_prefix, candidate_rest = _heading_prefix_key(candidate)
            if candidate_prefix != current_prefix or not candidate_rest:
                continue
            candidate_score = len(re.sub(r"\s+", "", candidate))
            if candidate_score <= best_score:
                continue
            if current_rest and not (
                current_rest in candidate_rest or candidate_rest in current_rest
            ):
                continue
            best_title = candidate
            best_score = candidate_score
        if best_title != item.title:
            reconciled.append(
                TocItem(
                    level=item.level,
                    title=best_title,
                    page_idx=item.page_idx,
                    page_estimate=item.page_estimate,
                    anchor=slugify(best_title),
                )
            )
        else:
            reconciled.append(item)
    return reconciled


def _toc_level_guidance_prompt() -> str:
    return (
        "Level guidance is mandatory and must be preserved exactly. "
        "Do not flatten levels. Infer level from the heading numbering pattern, "
        "not from visual spacing alone. "
        "Use these rules as the default mapping unless the document clearly "
        "shows a different hierarchy:\n"
        "- Ⅰ, Ⅱ, Ⅲ, I., II., III., 제1장, 제1절, 제1항 and similar top "
        "section markers => level 1.\n"
        "- 1., 2., 3., 가., 나., 다., A., B. and similar first child markers "
        "under a section => level 2.\n"
        "- 1.1, 1.2, 2.1, (1), (2), ① and similar second child markers => level 3.\n"
        "- 1.1.1, 1.1.2, 2.1.1 and deeper dotted numbers => level 4+ in order of depth.\n"
        "- If the document contains a visible parent-child chain, keep the chain "
        "in the output hierarchy exactly as seen.\n"
        "- A more specific numbering pattern must never be assigned the same "
        "level as its parent.\n"
        "- If two headings differ only by numbering depth, the deeper one must "
        "have a strictly larger level.\n"
        "- Do not convert all numbered headings into the same level just "
        "because they appear in the same page or section.\n"
        "Examples:\n"
        "  I. 제안 요청 개요 -> level 1\n"
        "  1. 사업 개요 -> level 2\n"
        "  1.1. 프로젝트 개요 -> level 3\n"
        "  1.1.1. 세부 과업 -> level 4\n"
        "Keep the page key for every item. Preserve order. Remove duplicates "
        "only when the title and hierarchy are truly identical.\n"
    )


def build_llm_toc_from_raw_document(
    raw_text: str, source_name: str, *, client: object | None = None
) -> list[TocItem]:
    """원문 텍스트에서 LLM으로 TOC 재구성. client 미지정 시 app.llm.factory로 생성."""
    from app.core.config import Settings
    from app.llm.base import Message
    from app.llm.factory import build_llm_client
    from prototype.v2.async_run import run_coro

    if client is None:
        client = build_llm_client(Settings())

    raw_text = raw_text or ""
    if not raw_text.strip():
        return []

    raw_text_original = raw_text
    txt_style = "document"
    if "txt" in source_name.lower():
        raw_text = _preprocess_txt_for_toc_llm(raw_text)
        txt_style = detect_txt_toc_style(raw_text_original)
        # Explicit TOC regions can carry misleading page numbers.
        # Strip the leading TOC block so page inference is driven by the body text.
        raw_text = _strip_leading_toc_region_from_txt(raw_text)

    system_prompt = (
        "You are an expert at reconstructing RFP table of contents from a raw document. "
        "Return only a JSON object with a single key rows, whose value is an array. "
        "Each array item must have keys: level(int 1-6), title(str), page(int|null). "
        "The page field is mandatory for every item and must never be omitted from "
        "the JSON object. "
        "If the document contains an explicit table of contents section, prefer the "
        "page numbers shown in that TOC section for TOC items. "
        "If the document does not contain an explicit table of contents section, "
        "reconstruct the structure from the headings in the body text and infer each "
        "page from the first page marker that appears after the matching heading. "
        "If a page number is available in the document text or can be inferred from "
        "the nearby context, include it; otherwise use null but still include the page key. "
        "When inferring from body text, match the heading first and then take the "
        "first page marker that appears after that heading in the document flow. "
        "Infer the table of contents from the entire document text, preserve "
        "hierarchy, and remove duplicates. "
        "Use only the supplied document text. Do not add explanations. "
        "If a title in the document has leading numbering, the title you return "
        "must include that numbering exactly in the title field. "
        "If the document contains a separate table of contents section, do not use "
        "the TOC's own page numbers as evidence for final page fields; prefer page "
        "markers that appear in the body text after the heading. "
        "If a section contains dense sub-hierarchies or many bullets (such as 가, "
        "나, 다, 1), 2), -, etc.), go one level deeper than usual to capture more "
        "granular sub-sections as TOC items, adding them with appropriate "
        "hierarchical levels (level 1-6) so no detailed content sections are missed. "
        + _toc_level_guidance_prompt()
    )
    if "txt" in source_name.lower() and txt_style == "ppt":
        system_prompt += (
            " The source is slide-style text from a presentation export. "
            "Short standalone slide titles are meaningful TOC entries even when "
            "page numbers are missing. "
            "Prefer title-like fragments over long prose sentences."
        )
    txt_style_prompt = (
        "This TXT source looks like a presentation export. "
        "Treat short slide titles as valid TOC entries and do not require page "
        "numbers for them.\n\n"
        if "txt" in source_name.lower() and txt_style == "ppt"
        else ""
    )
    base_user_prompt = (
        f"Extract the table of contents from the following {source_name} document text.\n"
        "Rules:\n"
        "1) Use the full document text.\n"
        "2) Preserve document order.\n"
        "3) Remove duplicates only when the title and hierarchy are truly the same.\n"
        "4) Keep hierarchy levels as accurately as possible and never flatten a "
        "deeper heading into the same level as its parent.\n"
        "5) Never omit the page key for any item.\n"
        "6) If a heading/title has numbering in the document, include that "
        "numbering in the returned title.\n"
        "7) If the document contains a TOC section, use the page numbers "
        "written in that TOC section for TOC items.\n"
        "8) If the document does not contain a TOC section, locate each "
        "heading in the body text and use the first page marker that appears "
        "after the matching heading.\n"
        "9) Do not use page numbers that belong to the TOC section itself as "
        "evidence for body-derived pages when the TOC and body disagree.\n"
        "10) If a section has multiple nested items or bullets, extract one "
        "more level of sub-bullets than usual to ensure detailed sub-items "
        "are captured.\n"
        "11) If a title starts with a bullet marker in the source, preserve "
        "that bullet-aware structure in the title so downstream card "
        "splitting can distinguish it.\n"
        "12) Follow the level guidance exactly.\n"
        "13) Return a JSON object with a single key rows (array) only.\n\n"
        f"{txt_style_prompt}"
        f"[RAW_DOCUMENT]\n{raw_text}"
    )
    prompt_attempts = [base_user_prompt]
    final_items: list[TocItem] = []
    attempt_idx = 0
    while attempt_idx < len(prompt_attempts):
        user_prompt = prompt_attempts[attempt_idx]

        async def _call(prompt: str = user_prompt) -> _TocRows:
            return await client.structured_output(
                [
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=prompt),
                ],
                _TocRows,
                purpose="rfpmatch_toc",
                max_tokens=12000,
            )

        result = run_coro(_call())
        final_rows = [
            {"level": row.level, "title": row.title, "page": row.page} for row in result.rows
        ]
        final_items = rows_to_toc_items(final_rows)
        if "txt" in source_name.lower():
            final_items = _normalize_txt_pages_to_estimates(final_items)
        final_items = _drop_unsequenced_bullet_items(final_items)
        is_txt_source = "txt" in source_name.lower()
        missing_sequences = find_missing_numbering_sequences(final_items) if is_txt_source else []
        if not is_txt_source or not missing_sequences or attempt_idx >= 1:
            break
        numbered_titles = [
            item.title for item in final_items if extract_leading_numbering(item.title)
        ]
        retry_prompt = (
            f"Rebuild the table of contents from the following {source_name} document text.\n"
            "Your previous TOC likely missed some numbering or omitted numbering "
            "prefixes in titles.\n"
            "Rules:\n"
            "1) Every title that has numbering in the document must include the "
            "numbering in the returned title.\n"
            "2) Review the numbering hierarchy in order and fill missing "
            "numbering items by searching the raw text again.\n"
            "3) Keep document order and preserve hierarchy.\n"
            "4) Never flatten a deeper heading into the same level as its parent.\n"
            "5) Never omit the page key for any item.\n"
            "6) If a section has multiple nested items or bullets, extract one "
            "more level of sub-bullets than usual to ensure detailed sub-items "
            "are captured.\n"
            "7) Follow the level guidance exactly.\n"
            "8) Return a JSON object with a single key rows (array) only.\n"
            f"Detected missing numbering sequences: {', '.join(missing_sequences)}.\n"
            f"Current numbered titles sample: "
            f"{json.dumps(numbered_titles[:40], ensure_ascii=False)}\n\n"
            f"[RAW_DOCUMENT]\n{raw_text}"
        )
        prompt_attempts.append(retry_prompt)
        attempt_idx += 1

    # 본문 전체 텍스트(raw_text_original)를 기준으로 비어있는 페이지 번호 스캔 보완 수행
    completed_items = _infer_missing_pages_from_body(final_items, raw_text_original)
    completed_items = _reconcile_titles_from_raw_text(completed_items, raw_text_original)
    completed_items = _promote_page_estimates(completed_items)
    return dedup_toc_items_by_title(completed_items)
