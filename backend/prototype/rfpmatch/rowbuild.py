"""텍스트→요구사항행 규칙 엔진 — step5 핵심. rfpmatch/step456_shared.py 이식.

카드의 본문/표 HTML을 (항목명/요구사항/상세요건) 행으로 뽑아내는 규칙 기반 엔진 전체.
확인된 죽은 코드(호출 0건, 34개 함수)는 제외했고, `_extract_hierarchical_body_rows`는
조기 return 뒤에 있던 665줄짜리 도달 불가능한 코드를 잘라냈다.
"""

from __future__ import annotations

import re
from collections import Counter

from bs4 import BeautifulSoup

from .models import RfpCard
from .partition import (
    _detect_bullet_level,
    _table_row_records_linewise,
    _table_visual_matrix,
)
from .sections import _title_match
from .text_utils import (
    _body_text_blocks,
    _cell_text_preserve_breaks,
    _html_excerpt_lines,
    _is_heading_like_text,
    _is_title_like_requirement_text,
    _merge_quoted_numeric_reference_spans,
    _normalize_requirement_text,
    _plain_text_from_html_excerpt,
)

_HEADER_LIKE_TERMS = {
    "표",
    "본문",
    "내용",
    "구분",
    "항목",
    "항목명",
    "상세내역",
    "상세요건",
    "상세내용",
    "세부내역",
    "세부내용",
    "요구사항",
}

_COMMON_INLINE_BODY_START_MARKERS = (
    "본 사업은",
    "본 사업의",
    "해당 사업은",
    "해당 사업의",
    "본 프로젝트는",
    "본 프로젝트의",
    "본 프로젝트와 관련한",
    "해당 프로젝트는",
    "해당 프로젝트의",
    "프로젝트는",
    "프로젝트의",
    "사업은",
    "사업의",
    "과업은",
    "과업의",
    "제안업체의 ",
    "제안업체는 ",
    "제안사의 ",
    "제안사는 ",
    "수행사의 ",
    "수행사는 ",
    "납품 솔루션은 ",
    "당사는 ",
    "다음과 같은 관점으로",
    "다음의 내용을 포함하여",
    "다음과 같이 기술합니다",
    "구축 완료 이후",
    "추후 ",
)


def _is_header_like_field(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    # 표/본문의 실제 항목 본문에 불릿이나 번호가 붙어 있으면
    # 헤더로 오인하지 않도록 강제적으로 header-like 판정을 제외한다.
    if _has_explicit_bullet_marker_text(normalized) or _has_quoted_bullet_marker(normalized):
        return False

    compact = re.sub(r"[\s\-\_/,:;()\[\]{}]+", "", normalized)
    if compact in _HEADER_LIKE_TERMS:
        return True

    parts = [re.sub(r"[\s\-\_/,:;()\[\]{}]+", "", part) for part in re.split(r"[\n|]+", normalized)]
    parts = [part for part in parts if part]
    if not parts:
        return False
    if all(part in _HEADER_LIKE_TERMS for part in parts):
        return True

    token_hits = sum(1 for term in _HEADER_LIKE_TERMS if term in compact)
    has_only_header_chars = re.fullmatch(r"[가-힣A-Za-z]+", compact or "") is not None
    return token_hits >= 2 and len(compact) <= 14 and has_only_header_chars


def _is_header_like_requirement_row(
    item_name: str, requirement: str, detail_requirement: str, result_note: str = ""
) -> bool:
    fields = [item_name, requirement, detail_requirement]
    if any(
        _has_explicit_bullet_marker_text(field) or _has_quoted_bullet_marker(field)
        for field in fields
    ):
        return False
    header_like_count = sum(1 for field in fields if _is_header_like_field(field))
    if header_like_count >= 2:
        return True

    non_empty_fields = [field for field in fields if _normalize_requirement_text(field)]
    if non_empty_fields and all(_is_header_like_field(field) for field in non_empty_fields):
        return True

    return bool(result_note and _is_header_like_field(result_note) and header_like_count >= 1)


def _is_redundant_same_text_requirement_row(
    item_name: str, requirement: str, detail_requirement: str
) -> bool:
    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)
    normalized_detail = _normalize_requirement_text(detail_requirement)
    if not normalized_item or not normalized_requirement or not normalized_detail:
        return False
    return normalized_item == normalized_requirement == normalized_detail


def _detail_dedup_key_text(value: str) -> str:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return ""
    # Keep the leading numbering / bullet marker here.
    # These prefixes can distinguish separate hierarchical items such as
    # "(9.3)" and "(9.10)", which must not collapse into one row.
    return normalized


def _strip_trailing_orphan_bullet(value: str, source_tag: str = "") -> str:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return ""

    # Avoid catastrophic backtracking on OCR-heavy dash runs such as
    # "------------------------------------------------ 23.4 ~ 23.6".
    bullet_only_pattern = re.compile(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*\s]+$")
    cleaned_lines: list[str] = []
    for line in normalized.split("\n"):
        compact_line = _normalize_requirement_text(line)
        if not compact_line:
            continue
        if bullet_only_pattern.fullmatch(compact_line):
            continue
        cleaned_lines.append(compact_line)

    if not cleaned_lines:
        return ""

    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"\s+[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+$", "", cleaned).strip()
    if str(source_tag or "").lower() == "li":
        if bullet_only_pattern.fullmatch(cleaned):
            return ""
        return cleaned
    if str(source_tag or "").lower() not in {"본문", "body"}:
        # 문장 끝에 붙은 "가.", "나)", "ㄱ." 같은 잔여 마커는 불릿이 아니라
        # OCR/줄바꿈 잔상으로 보고 제거한다.
        cleaned = re.sub(
            r"\s+(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]\s*$",
            "",
            cleaned,
        ).strip()
    cleaned = re.sub(r"[,\u3001\uFF0C]\s*$", "", cleaned).strip()
    if bullet_only_pattern.fullmatch(cleaned):
        return ""
    return cleaned


def _normalize_middle_dot_connectors(value: str) -> str:
    text = _normalize_requirement_text(value)
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _has_inline_middle_dot_connector(value: str) -> bool:
    text = _normalize_requirement_text(value)
    if not text:
        return False
    return bool(re.search(r"(?<=\S)\s*[·ㆍ•◦]\s*(?=\S)", text))


def _split_inline_standalone_bullet_units(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []
    if _looks_like_leading_bullet_connector_phrase(normalized):
        return [normalized]
    if _looks_like_dash_connected_term_chain(normalized):
        return [normalized]
    if _looks_like_asterisk_quantity_expression(normalized):
        return [normalized]
    pattern = re.compile(r"\s+(?P<marker>[\-*▪■◆▶○□◇⦁−–—]+)\s+(?=\S)")
    match = pattern.search(normalized)
    if not match:
        return [normalized]
    left = _normalize_requirement_text(normalized[: match.start()])
    # 뒤쪽 조각은 marker를 유지해야 다음 재귀에서 또 다른 불릿으로 판정할 수 있다.
    right = _normalize_requirement_text(normalized[match.start() :])
    units: list[str] = []
    if left:
        units.extend(_split_inline_standalone_bullet_units(left))
    if right:
        units.extend(_split_inline_standalone_bullet_units(right))
    return units or [normalized]


def _looks_like_asterisk_quantity_expression(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized or "\n" in normalized:
        return False
    if " * " not in normalized:
        return False
    return bool(
        re.search(
            r"\b[가-힣A-Za-z0-9/()_-]{1,30}\s+\*\s+(?:\d+|[0-9]+(?:EA|개|식|대|포트|GB|TB|U)?)\b",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _split_repeated_bullet_chain(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []
    if _is_reference_like_body_line(normalized):
        return [normalized]
    # 표 안에서 반복되는 불릿 체인은 각 불릿을 별도 상세요건으로 분리한다.
    bullet_pattern = re.compile(r"(?=(?:^|\s)(?:[•▪■◆▶◦○□◇⦁])\s+\S)")
    hits = list(bullet_pattern.finditer(normalized))
    if len(hits) <= 1:
        return [normalized]
    pieces: list[str] = []
    starts = [hit.start() for hit in hits]
    starts.append(len(normalized))
    for idx in range(len(starts) - 1):
        chunk = _normalize_requirement_text(normalized[starts[idx] : starts[idx + 1]])
        if chunk:
            pieces.append(chunk)
    return pieces or [normalized]


def _has_quoted_bullet_marker(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    quoted_patterns = [
        r"『[^』]*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^』]*?』",
        r"「[^」]*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^」]*?」",
        r"“[^”]*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^”]*?”",
        r"\"[^\"]*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^\"]*?\"",
        r"'[^']*?(?:[\-*•▪■◆▶◦○□◇⦁])\s+[^']*?'",
    ]
    return any(re.search(pattern, normalized) for pattern in quoted_patterns)


def _looks_like_connected_dash_phrase(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized or "\n" in normalized:
        return False
    separator_pattern = r"(?:->|→|[-–—])"
    if not re.search(rf"\s{separator_pattern}\s", normalized):
        return False
    if re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return False

    parts = [part.strip() for part in re.split(r"\s*(?:->|→|[-–—])\s*", normalized) if part.strip()]
    if len(parts) < 2 or len(parts) > 4:
        return False
    if any(
        re.match(
            r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
            part,
            flags=re.IGNORECASE,
        )
        for part in parts
    ):
        return False
    if _looks_like_sentence_text(normalized):
        return False
    return all(len(part) <= 20 for part in parts)


def _looks_like_leading_bullet_connector_phrase(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized or "\n" in normalized:
        return False
    if not re.match(r"^\s*[\-*▪■◆▶◦○□◇⦁·ㆍ−–—]+\s+\S", normalized):
        return False
    body = re.sub(r"^\s*[\-*▪■◆▶◦○□◇⦁·ㆍ−–—]+\s+", "", normalized, count=1).strip()
    if not body:
        return False
    if not re.search(r"\s(?:->|→|[-–—])\s", body):
        return False
    return not re.search(
        r"(?:^|\s)(?:[•▪■◆▶◦○□◇⦁\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        body,
        flags=re.IGNORECASE,
    )


def _looks_like_dash_connected_term_chain(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized or "\n" in normalized:
        return False
    separator_pattern = r"(?:->|→|[-–—])"
    if not re.search(rf"\s{separator_pattern}\s", normalized):
        return False
    if re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return False

    parts = [part.strip() for part in re.split(r"\s*(?:->|→|[-–—])\s*", normalized) if part.strip()]
    if len(parts) < 2 or len(parts) > 5:
        return False
    if any(
        re.match(
            r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
            part,
            flags=re.IGNORECASE,
        )
        for part in parts
    ):
        return False

    def _looks_like_term(part: str, *, allow_suffix: bool = False) -> bool:
        compact = _normalize_requirement_text(part)
        if not compact:
            return False
        if re.search(r"[,:;]", compact):
            return False
        if allow_suffix:
            return bool(
                re.fullmatch(
                    r"[가-힣A-Za-z0-9/()]{1,20}(?:\s+[가-힣A-Za-z0-9/()]{1,20}){0,5}(?:까지|까지의|연결|흐름|단계|절차|프로세스|기능|처리|연계|전환|구성|방식)?",
                    compact,
                )
            )
        return bool(
            re.fullmatch(
                r"[가-힣A-Za-z0-9/()]{1,20}(?:\s+[가-힣A-Za-z0-9/()]{1,20}){0,2}",
                compact,
            )
        )

    if not all(_looks_like_term(part) for part in parts[:-1]):
        return False
    return _looks_like_term(parts[-1], allow_suffix=True)


def _split_inline_numbered_marker_units(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []
    pattern = re.compile(
        r"(?:(?<=^)|(?<=\s)|(?<=[\.\!\?\:\;，,、\)\]\}]))(?P<marker>\(?\d+(?:\.\d+)*[\)\.]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.]|[IVXLCDM]+[\)\.])\s+(?=\S)",
        flags=re.IGNORECASE,
    )
    matches = list(pattern.finditer(normalized))
    if len(matches) <= 1:
        return [normalized]
    units: list[str] = []
    start = 0
    for idx, match in enumerate(matches):
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
        chunk = _normalize_requirement_text(normalized[match.start() : next_start])
        if chunk:
            units.append(chunk)
        start = next_start
    if not units:
        return [normalized]
    if start < len(normalized):
        tail = _normalize_requirement_text(normalized[start:])
        if tail and tail not in units:
            units.append(tail)
    return units


def _collapse_repeated_token_tail(value: str) -> str:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return ""
    prefix_match = re.match(
        r"^(?P<prefix>(?:[ㄱ-ㅎ가-하][\.\)]|(?:\(?\d+(?:\.\d+)*[\)\.]?)|(?:[A-Za-z]|[IVXLCDM]+)[\.\)])\s+)(?P<body>.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not prefix_match:
        return normalized
    prefix = _normalize_requirement_text(prefix_match.group("prefix"))
    body = _normalize_requirement_text(prefix_match.group("body"))
    tokens = [token for token in body.split() if token]
    if len(tokens) >= 4 and len(tokens) % 2 == 0:
        half = len(tokens) // 2
        if tokens[:half] == tokens[half:]:
            return _normalize_requirement_text(f"{prefix} {' '.join(tokens[:half])}")
    return normalized


def _is_marker_only_requirement_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    return bool(
        re.fullmatch(r"[ㄱ-ㅎ가-하]\.?", compact)
        or re.fullmatch(r"\(?\d+\)?[\.\)]?", compact)
        or re.fullmatch(r"[A-Za-z][\.\)]?", compact)
        or re.fullmatch(r"[IVXLCDM]+[\.\)]?", compact, flags=re.IGNORECASE)
        or re.fullmatch(r"[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+", compact)
    )


def _looks_like_sentence_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    if len(normalized) >= 40:
        return True
    sentence_markers = [
        "본 사업은",
        "해당 사업은",
        "당사는",
        "해야",
        "필요",
        "제공",
        "지원",
        "구축",
        "적용",
        "준수",
        "가능",
        "수행",
        "제출",
        "이행",
    ]
    return any(marker in normalized for marker in sentence_markers) and len(normalized.split()) >= 4


def _is_item_name_like_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    if _is_title_like_requirement_text(normalized):
        return True
    if _looks_like_sentence_text(normalized):
        return False
    compact = re.sub(r"\s+", "", normalized)
    return bool(normalized and len(compact) <= 24 and len(normalized.split()) <= 5)


def _has_descendant_bullets(node: dict) -> bool:
    stack = list(node.get("children") or [])
    while stack:
        child = stack.pop()
        text = _normalize_requirement_text(str(child.get("text") or ""))
        if re.match(r"^[\-*•▪■◆▶◦○□◇·ㆍ−–—]+\s*", text):
            return True
        stack.extend(list(child.get("children") or []))
    return False


def _should_skip_requirement_extraction(card: RfpCard) -> tuple[bool, str]:
    title = str(getattr(card, "subject", None) or card.requirement or "")
    group = str(getattr(card, "part", None) or "")
    section = str(getattr(card, "section", None) or "")
    sub_subject = str(getattr(card, "sub_subject", None) or "")

    combined_text = " | ".join([title, group, section, sub_subject]).strip()

    # 1. 카드 제목(title) 또는 상위 파트(part) 내에서만 검사하는 키워드 (공백 제거 후 비교)
    target_text_for_group = f"{title} | {group}"
    normalized_group_text = re.sub(r"\s+", "", target_text_for_group)

    group_specific_keywords = (
        "제안서 작성 및 유의사항",
        "제안서 작성 방안",
        "제안 일반 사항",
        "제안 일반사항",
    )
    for keyword in group_specific_keywords:
        normalized_keyword = re.sub(r"\s+", "", keyword)
        if normalized_keyword in normalized_group_text:
            return True, f"excluded_by_part_keyword_{keyword}"

    # 2. 전체 combined_text에서 검사하는 일반 키워드 (공백 제거 후 비교)
    normalized_combined = re.sub(r"\s+", "", combined_text)
    general_keywords = (
        "서식",
        "별첨",
        "입찰 안내",
        "담당자",
        "입찰 일반사항",
        "제안 요청 및 지침",
        "제안 서식 및 별첨",
        "제안 요청서의 효력",
        "유의 사항",
        "제안업체 기본요건",
        "제안서 작성 기준",
        "제안서 작성 방안",
        "제안 일반 사항",
        "제안 일반사항",
        "제출 서류",
        "제안서 평가",
        "선정 방안",
        "기타사항",
        "가격 제안",
        "입찰 제안",
    )
    for keyword in general_keywords:
        normalized_keyword = re.sub(r"\s+", "", keyword)
        if normalized_keyword in normalized_combined:
            return True, f"excluded_by_keyword_{keyword}"

    return False, ""


def _normalize_item_name_for_row(item_name: str, requirement: str = "") -> str:
    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)

    candidates: list[str] = []
    for source in (normalized_item, normalized_requirement):
        if not source:
            continue
        heading, detail_lines = _split_body_heading_and_detail_lines(source)
        if heading and heading != source and _is_title_like_requirement_text(heading):
            candidates.append(heading)
            continue
        heading2, inline_details = _split_inline_heading_and_body(source)
        if (
            heading2
            and heading2 != source
            and (_is_title_like_requirement_text(heading2) or detail_lines or inline_details)
        ):
            candidates.append(heading2)
            continue
        if _is_title_like_requirement_text(source):
            candidates.append(source)

    for candidate in candidates:
        if candidate:
            return candidate

    if normalized_item:
        return normalized_item
    if normalized_requirement:
        return normalized_requirement
    return ""


def _flatten_body_requirement_for_save(
    item_name: str,
    requirement: str,
    detail_requirement: str,
    *,
    build_source: str = "",
    special_rule_applied: bool = False,
) -> tuple[str, str]:
    normalized_source = _normalize_requirement_text(build_source)
    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)
    normalized_detail = _normalize_requirement_text(detail_requirement)
    if normalized_source != "본문":
        return normalized_requirement, normalized_detail
    # 본문은 줄 단위 atomic 출력이 원칙이므로 요구사항을 상세요건에 다시 붙이지 않는다.
    normalized_requirement = normalized_item or normalized_requirement
    if special_rule_applied and normalized_item:
        normalized_requirement = normalized_item
    return normalized_requirement, normalized_detail


def _fallback_item_name_for_marker_only_row(
    item_name: str,
    category_text: str,
    section_title_text: str,
    default_item_name: str,
) -> str:
    normalized_item = _normalize_requirement_text(item_name)
    if not normalized_item:
        return _normalize_requirement_text(default_item_name or category_text or section_title_text)
    if not _is_marker_only_requirement_text(normalized_item):
        return normalized_item
    return _normalize_requirement_text(
        default_item_name or category_text or section_title_text or normalized_item
    )


def _describe_build_method(
    build_method: str,
    build_source: str,
    *,
    item_name: str = "",
    requirement: str = "",
    detail_requirement: str = "",
) -> tuple[str, str]:
    normalized_method = _normalize_requirement_text(build_method)
    normalized_source = _normalize_requirement_text(build_source)
    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)
    normalized_detail = _normalize_requirement_text(detail_requirement)
    short_method = normalized_method or ("표" if normalized_source == "표" else "본문")
    table_branch_label = ""
    if normalized_source == "표":
        if "룰 기반(2단 표)" in short_method:
            table_branch_label = "2단표"
        elif (
            "룰 기반(3단 표->2단 표+추가정보)" in short_method
            or "룰 기반(3단 표-그룹헤더)" in short_method
        ):
            table_branch_label = "3단표"
        elif (
            "룰 기반(4단 표->3단 표+추가정보)" in short_method
            or "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)" in short_method
            or "룰 기반(4단 표)" in short_method
        ):
            table_branch_label = "4단표"
        elif "룰 기반(표 fallback)" in short_method:
            table_branch_label = "표fallback"
        elif short_method == "표":
            table_branch_label = "표"
    if (
        "룰 기반(본문 계층 정규화)" in short_method
        or "룰 기반(단일 행 본문)" in short_method
        or "룰 기반(HTML 계층 정규화)" in short_method
        or "룰 기반(표전 본문)" in short_method
    ):
        short_method = "본문룰(계층 없음 / atomic)"
    elif normalized_source != "표" and "룰 기반(표 fallback)" in short_method:
        short_method = "표fallback"
    elif (
        "LLM(표후속공통주석)" in short_method
        or "LLM+구조정규화" in short_method
        or short_method == "LLM"
    ):
        short_method = "LLM"
    elif normalized_source == "본문":
        short_method = "본문룰(계층 없음 / atomic)"

    applied_rules: list[str] = []
    if normalized_source == "표":
        applied_rules.append("표룰")
        applied_rules.append("표구조인식")
        applied_rules.append("번호열/중복헤더보정")
        applied_rules.append("표칼럼매핑")
        if table_branch_label:
            applied_rules.append(table_branch_label)
    elif "LLM" in normalized_method:
        applied_rules.append("LLM룰")
        applied_rules.append("본문/표재정리")
        applied_rules.append("중복제거정규화")
    else:
        applied_rules.append("본문룰(계층 없음 / atomic)")
        applied_rules.append("atomic평탄화")
        applied_rules.append("불릿/번호 계층 비적용")
        applied_rules.append("정규화")

    if normalized_method.startswith("룰 기반("):
        applied_rules.append(normalized_method)
    if short_method in {"2단표", "3단표", "4단표"}:
        applied_rules.append(short_method)
    if "HTML" in normalized_method:
        applied_rules.append("HTML계층반영")
    if "본문" in normalized_method:
        applied_rules.append("본문줄바꿈/불릿정리")
    if "단일 행" in normalized_method:
        applied_rules.append("단일행분해")
    if "표전" in normalized_method:
        applied_rules.append("표전본문")
    if "fallback" in normalized_method.lower():
        applied_rules.append("fallback")
    if normalized_item:
        applied_rules.append("항목명정규화")
    if normalized_requirement:
        applied_rules.append("요구사항정규화")
    if normalized_detail:
        applied_rules.append("상세요건정리")

    applied_rule = " > ".join(applied_rules)
    return short_method, applied_rule


def _build_source_detail_label(
    build_source: str,
    build_method: str,
    *,
    item_name: str = "",
    requirement: str = "",
    detail_requirement: str = "",
    source_hint: str = "",
) -> str:
    hint = _normalize_requirement_text(source_hint)
    method = _normalize_requirement_text(build_method)
    source = _normalize_requirement_text(build_source)
    item = _normalize_requirement_text(item_name)
    requirement_text = _normalize_requirement_text(requirement)
    detail = _normalize_requirement_text(detail_requirement)

    if source == "표":
        table_label = _format_table_source_detail_label(method, hint)
        if table_label:
            return table_label
    if hint:
        return hint

    if source == "표":
        return "표"

    if "1단 표" in method:
        return "1단표"
    if "룰 기반(2단 표)" in method:
        return "2단표"
    if "룰 기반(3단 표->2단 표+추가정보)" in method:
        return "특수 2단 + 내용"
    if "룰 기반(3단 표-그룹헤더)" in method:
        return "특수 3단 + 내용"
    if "룰 기반(넘버링 표->3단 처리)" in method:
        return "특수 3단 + 내용"
    if "룰 기반(3단 표)" in method:
        return "3단표"
    if "룰 기반(4단 표->3단 표+추가정보)" in method:
        return "특수 4단 + 내용"
    if "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)" in method:
        return "특수 4단 + 내용"
    if "룰 기반(4단 표)" in method:
        return "4단표"
    if "표 fallback" in method:
        return "표 fallback"

    if source == "본문":
        if "LLM" in method:
            return "LLM"
        if (
            "단일 행 본문" in method
            or "본문 계층 정규화" in method
            or "HTML 계층 정규화" in method
            or "표전 본문" in method
        ):
            return "계층 없음 / atomic"
        return "계층 없음 / atomic"

    if "단일 행 본문" in method:
        return "계층 없음 / atomic"
    if "\n" in detail:
        line_count = len([line for line in detail.split("\n") if _normalize_requirement_text(line)])
        if line_count >= 3:
            return "항목명, 요구사항, 상세요건, 상세요건"
        if line_count == 2:
            return "항목명, 요구사항, 상세요건, 상세요건"
    if item and item == requirement_text:
        if detail:
            return "계층 없음 / atomic"
        return "계층 없음 / atomic" if source == "본문" else (source or "룰 기반")
    if requirement_text and requirement_text == detail:
        return "계층 없음 / atomic"
    if (
        item
        and requirement_text
        and detail
        and item != requirement_text
        and requirement_text != detail
        and ("본문 계층 정규화" in method or "HTML 계층 정규화" in method or "표전 본문" in method)
    ):
        return "항목명, 요구사항, 상세요건"
    if "LLM" in method:
        return "LLM"
    return "항목명, 요구사항, 상세요건" if source == "본문" else (source or "룰 기반")


def _format_table_source_detail_label(method: str, source_hint: str = "") -> str:
    hint = _normalize_requirement_text(source_hint)
    if hint.startswith("특수 "):
        return hint
    if hint in {"1단표", "2단표", "3단표", "4단표"}:
        return hint

    normalized_method = _normalize_requirement_text(method)
    if "룰 기반(1단 표" in normalized_method:
        return "1단표"
    if "룰 기반(2단 표)" in normalized_method:
        return "2단표"
    if "룰 기반(3단 표)" in normalized_method:
        return "3단표"
    if "룰 기반(4단 표)" in normalized_method:
        return "4단표"
    if "룰 기반(3단 표->2단 표+추가정보)" in normalized_method:
        return "특수 2단 + 내용"
    if "룰 기반(3단 표-그룹헤더)" in normalized_method:
        return "특수 3단 + 내용"
    if "룰 기반(넘버링 표->3단 처리)" in normalized_method:
        return "특수 3단 + 내용"
    if "룰 기반(4단 표->3단 표+추가정보)" in normalized_method:
        return "특수 4단 + 내용"
    if "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)" in normalized_method:
        return "특수 4단 + 내용"
    if "표 fallback" in normalized_method:
        return "표 fallback"
    return hint


def _table_rule_branch_label(build_method: str) -> str:
    normalized_method = _normalize_requirement_text(build_method)
    if not normalized_method:
        return ""
    if "룰 기반(3단 표->2단 표+추가정보)" in normalized_method:
        return "3단표->2단표+비고/추가정보"
    if "룰 기반(3단 표-그룹헤더)" in normalized_method:
        return "3단표-그룹헤더"
    if "룰 기반(4단 표->3단 표+추가정보)" in normalized_method:
        return "4단표->3단표+추가정보"
    if "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)" in normalized_method:
        return "4단표-번호컬럼제거후3단처리"
    if "룰 기반(4단 표)" in normalized_method:
        return "4단표"
    if "룰 기반(3단 표-박스형 본문)" in normalized_method:
        return "3단표-박스형본문"
    if "룰 기반(넘버링 표->3단 처리)" in normalized_method:
        return "넘버링 표->3단 처리"
    if "룰 기반(2단 표)" in normalized_method:
        return "2단표"
    if "룰 기반(3단 표-단일행 fallback)" in normalized_method:
        return "3단표-단일행fallback"
    if "표 fallback" in normalized_method:
        return "표 fallback"
    if "일반3단표" in normalized_method:
        return "일반3단표"
    return ""


def _format_debug_build_source_label(build_source: str) -> str:
    source = _normalize_requirement_text(build_source)
    if source == "본문":
        return "본문룰(계층 없음 / atomic)"
    return source or ""


def _format_body_source_detail_label(
    *,
    item_name: str = "",
    requirement: str = "",
    detail_requirement: str = "",
    bullet_count: int = 0,
    fragment_level: int | None = None,
) -> str:
    return "계층 없음 / atomic"


def _expand_requirement_rows(rows: list[dict]) -> list[dict]:
    expanded: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item_name = _normalize_requirement_text(str(row.get("item_name") or ""))
        requirement = _normalize_requirement_text(str(row.get("requirement") or ""))
        detail_requirement = _normalize_requirement_text(str(row.get("detail_requirement") or ""))
        result_note = _normalize_requirement_text(str(row.get("result_note") or ""))
        if not detail_requirement:
            continue

        detail_units = _split_atomic_detail_units(detail_requirement) or [detail_requirement]
        for detail_unit in detail_units:
            cleaned = _strip_trailing_orphan_bullet(_normalize_requirement_text(detail_unit))
            build_method_text = _normalize_requirement_text(str(row.get("build_method") or ""))
            if not cleaned or _is_only_page_info(cleaned):
                continue
            if _is_marker_only_requirement_text(cleaned) and "표" not in build_method_text:
                continue
            cleaned = _normalize_requirement_text(cleaned)
            if _is_redundant_same_text_requirement_row(item_name, requirement, cleaned):
                continue
            dedup_detail = _detail_dedup_key_text(cleaned)
            key = (item_name, requirement, dedup_detail, result_note)
            if key in seen:
                continue
            seen.add(key)
            expanded.append(
                {
                    **row,
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": cleaned,
                    "result_note": result_note,
                }
            )
    return expanded


def _strip_leading_numbering(value: str) -> str:
    compact = _normalize_requirement_text(value)
    if not compact:
        return ""
    stripped = re.sub(
        r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
        "",
        compact,
        flags=re.IGNORECASE,
    )
    return stripped.strip() or compact


def _has_leading_numbering(value: str) -> bool:
    compact = _normalize_requirement_text(value)
    if not compact:
        return False
    return bool(
        re.match(
            r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
            compact,
            flags=re.IGNORECASE,
        )
    )


def _drop_numbering_column_from_matrix(
    matrix: list[list[str]],
) -> tuple[list[list[str]], int | None]:
    if not matrix:
        return matrix, None
    max_cols = max(len(row) for row in matrix)
    if max_cols not in {4, 5}:
        return matrix, None

    best_col: int | None = None
    best_score = -1.0
    for col_idx in range(max_cols):
        values = [str(row[col_idx]).strip() for row in matrix if col_idx < len(row)]
        non_empty = [value for value in values if value]
        if len(non_empty) < max(2, len(values) // 2):
            continue
        numbering_like = sum(
            1
            for value in non_empty
            if re.fullmatch(r"\(?\d+(?:\.\d+)*[\)\.]?", value) is not None
            or (_has_leading_numbering(value) and len(_strip_leading_numbering(value)) <= 2)
        )

        # 헤더 셀이 명백히 순번을 나타내는 키워드인 경우 가중치를 주어
        # 데이터 행이 적은 소형 표에서도 드롭이 활성화되도록 유도
        header_val = values[0] if values else ""
        header_bonus = 0.0
        if re.sub(r"\s+", "", header_val).lower() in {
            "순번",
            "번호",
            "no",
            "no.",
            "num",
            "number",
            "id",
        }:
            header_bonus = 0.3

        score = (numbering_like / max(len(non_empty), 1)) + header_bonus
        if score >= 0.7 and score > best_score:
            best_score = score
            best_col = col_idx

    if best_col is None:
        return matrix, None

    reduced: list[list[str]] = []
    for row in matrix:
        reduced.append([cell for idx, cell in enumerate(row) if idx != best_col])
    return reduced, best_col


def _split_atomic_detail_units(value: str) -> list[str]:
    raw_value = str(value or "")
    raw_lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
    if len(raw_lines) >= 2:
        first_line = _normalize_requirement_text(raw_lines[0])
        second_line = _normalize_requirement_text(raw_lines[1])
        if re.match(r"^[\-*·ㆍ−–—]+\s*.+$", first_line) and re.match(
            r"^M\s*-\s*\d+(?:\s*(?:개월|주|일|개월차|주차|월|년|단계|시점|전|후))?.+$",
            second_line,
            flags=re.IGNORECASE,
        ):
            return [_normalize_requirement_text(f"{first_line} {second_line}")]

    normalized = _normalize_middle_dot_connectors(value)
    if not normalized:
        return []
    if _has_quoted_bullet_marker(normalized):
        return [normalized]
    if _looks_like_asterisk_quantity_expression(normalized):
        return [normalized]
    repeated_bullet_units = _split_repeated_bullet_chain(normalized)
    if len(repeated_bullet_units) > 1:
        return repeated_bullet_units
    standalone_bullet_units = _split_inline_standalone_bullet_units(normalized)
    if len(standalone_bullet_units) > 1:
        return standalone_bullet_units

    def _looks_like_hyphenated_noun_phrase(text: str) -> bool:
        compact = _normalize_requirement_text(text)
        if not compact or "\n" in compact:
            return False
        if _looks_like_sentence_text(compact):
            return False
        if any(token in compact for token in ["해야", "필요", "분리", "제시", "구성", "협의"]):
            return False
        parts = [part.strip() for part in re.split(r"\s+[-–—]\s+", compact) if part.strip()]
        if len(parts) != 2:
            return False
        if any(re.search(r"[-–—]", part) for part in parts):
            return False
        return all(
            re.fullmatch(r"[가-힣A-Za-z0-9/()]{1,20}(?:\s+[가-힣A-Za-z0-9/()]{1,20}){0,2}", part)
            for part in parts
        )

    # 단일 대시로 연결된 짧은 명사구만 예외로 유지한다.
    # 그 외의 문장 중간 대시는 계속 분리 대상으로 본다.
    if _looks_like_hyphenated_noun_phrase(normalized):
        return [normalized]
    if _looks_like_connected_dash_phrase(normalized):
        return [normalized]

    numbered_marker_units = _split_inline_numbered_marker_units(normalized)
    if len(numbered_marker_units) > 1:
        return numbered_marker_units
    repeated_bullet_units = _split_repeated_bullet_chain(normalized)
    if len(repeated_bullet_units) > 1:
        return repeated_bullet_units
    standalone_bullet_units = _split_inline_standalone_bullet_units(normalized)
    if len(standalone_bullet_units) > 1:
        return standalone_bullet_units
    if _has_inline_middle_dot_connector(normalized) and not re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return [normalized]
    if _is_reference_like_body_line(normalized):
        return [normalized]
    if _has_inline_numeric_bullet_not_at_start(normalized):
        return [normalized]

    # OCR 결과가 "제 3 자", "제 3 자의"처럼 잘못 띄어지는 경우가 많다.
    # 이 상태로 두면 "3"을 목록 번호 마커로 오인해서 상세요건이 비정상 분리된다.
    normalized = re.sub(r"제\s+(\d+)\s+자", r"제\1자", normalized)

    # 문장형으로 보이더라도 반복 불릿이 섞여 있으면 먼저 분리한다.
    # 예: "① 검색 기능 • ... • ..." 같은 표/본문 혼합 케이스.
    repeated_bullet_units = _split_repeated_bullet_chain(normalized)
    if len(repeated_bullet_units) > 1:
        return repeated_bullet_units
    standalone_bullet_units = _split_inline_standalone_bullet_units(normalized)
    if len(standalone_bullet_units) > 1:
        return standalone_bullet_units

    if _looks_like_sentence_text(normalized) and not re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return [normalized]

    korean_chain_split = _split_inline_korean_letter_requirements([normalized])
    if len(korean_chain_split) > 1:
        return [unit for unit in korean_chain_split if unit]

    # 단일 마커 줄은 마커와 본문을 분리하지 말고 한 단위로 유지한다.
    # 예: "ㄴ. 설명"을 ["ㄴ.", "설명"]으로 쪼개지 않게 한다.
    if re.match(
        r"^\s*(?:[가-하ㄱ-ㅎ]|[A-Za-z]|[IVXLCDM]+)[\.\)]\s+.+$", normalized, flags=re.IGNORECASE
    ):
        marker_hits = len(
            re.findall(
                r"(?:^|\s)(?:[가-하ㄱ-ㅎ]|[A-Za-z]|[IVXLCDM]+)[\.\)]\s+",
                normalized,
                flags=re.IGNORECASE,
            )
        )
        if marker_hits <= 1:
            return [normalized]

    def merge_numbered_middle_dot_chain(units: list[str]) -> list[str]:
        if len(units) <= 1:
            return units
        merged: list[str] = []
        idx = 0
        numbered_pattern = re.compile(
            r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
            flags=re.IGNORECASE,
        )
        middle_dot_pattern = re.compile(r"^\s*[·ㆍ]\s*(.+?)\s*$")
        while idx < len(units):
            current = _normalize_requirement_text(units[idx])
            if not current:
                idx += 1
                continue
            if numbered_pattern.match(current):
                chain = [current]
                look_ahead = idx + 1
                dot_count = 0
                while look_ahead < len(units):
                    candidate = _normalize_requirement_text(units[look_ahead])
                    dot_match = middle_dot_pattern.match(candidate)
                    if not dot_match:
                        break
                    dot_count += 1
                    chain.append(candidate)
                    look_ahead += 1
                if dot_count >= 2:
                    merged.append(" ".join(chain).strip())
                    idx = look_ahead
                    continue
            merged.append(current)
            idx += 1
        return merged

    raw_lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    marker_prefix_pattern = (
        r"(?:[①-⑳㉠-㉾❶-❿]+|[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|"
        r"\(?\d+(?:\.\d+)*[\)\.\-]|"
        r"(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|"
        r"[IVXLCDM]+[\)\.\-])"
    )
    inline_marker_prefix_pattern = (
        r"(?:[①-⑳㉠-㉾❶-❿]+|"
        r"(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|"
        r"[IVXLCDM]+[\)\.\-]|"
        r"[▪■◆▶○□◇⦁−–—\-\*]+)"
    )
    inline_split_pattern = re.compile(
        r"(?:(?<=^)|(?<=\s)|(?<=[\.\!\?\:\;，,、\)\]\}]))(?P<marker>"
        + inline_marker_prefix_pattern
        + r")\s*",
        flags=re.IGNORECASE,
    )

    def split_inline_markers(line: str) -> list[str]:
        text = _normalize_requirement_text(line)
        if not text:
            return []
        if _looks_like_leading_bullet_connector_phrase(
            text
        ) or _looks_like_dash_connected_term_chain(text):
            return [text]
        matches = list(inline_split_pattern.finditer(text))
        if len(matches) <= 1:
            return [text]
        pieces: list[str] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            chunk = _normalize_requirement_text(text[start:end])
            if chunk:
                pieces.append(chunk)
        return pieces or [text]

    expanded_lines: list[str] = []
    for line in raw_lines:
        expanded_lines.extend(split_inline_markers(line))
    raw_lines = expanded_lines or raw_lines

    has_explicit_marker_lines = any(
        re.match(rf"^{marker_prefix_pattern}\s*", line, flags=re.IGNORECASE) for line in raw_lines
    )
    if has_explicit_marker_lines:
        units: list[str] = []
        preamble_lines: list[str] = []
        current_unit = ""
        for line in raw_lines:
            marker_only = re.match(
                rf"^(?P<prefix>{marker_prefix_pattern})\s*$",
                line,
                flags=re.IGNORECASE,
            )
            marker_start = re.match(
                rf"^(?P<prefix>{marker_prefix_pattern})\s*(?P<body>.+)$",
                line,
                flags=re.IGNORECASE,
            )
            if marker_only:
                if current_unit:
                    units.append(current_unit.strip())
                elif preamble_lines:
                    units.append(" ".join(preamble_lines).strip())
                    preamble_lines = []
                current_unit = marker_only.group("prefix").strip()
                continue
            if marker_start:
                if current_unit:
                    units.append(current_unit.strip())
                elif preamble_lines:
                    units.append(" ".join(preamble_lines).strip())
                    preamble_lines = []
                current_unit = line
                continue
            if current_unit:
                current_unit = f"{current_unit} {line}".strip()
            else:
                preamble_lines.append(line)
        if current_unit:
            units.append(current_unit.strip())
        elif preamble_lines:
            units.append(" ".join(preamble_lines).strip())
        return [unit for unit in merge_numbered_middle_dot_chain(units) if unit]

    units: list[str] = []
    pending_prefix = ""
    for line in raw_lines:
        if not line:
            continue
        marker_only = re.match(
            rf"^(?P<prefix>{marker_prefix_pattern})\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if marker_only:
            pending_prefix = marker_only.group("prefix").strip()
            continue
        if pending_prefix:
            units.append(f"{pending_prefix} {line}".strip())
            pending_prefix = ""
            continue
        units.append(line)

    if pending_prefix:
        units.append(pending_prefix)

    if len(units) > 1:
        return merge_numbered_middle_dot_chain(units)

    single = units[0] if units else normalized
    if re.match(rf"^\s*{marker_prefix_pattern}\s*", single, flags=re.IGNORECASE):
        return [single.strip()]
    sentence_parts = [
        part.strip()
        for part in re.split(
            r"(?<![가-힣A-Za-z0-9]\.)(?<=[\.\!\?])\s+|(?<=함)\s+(?=[\-•▪■◆▶◦○□◇])", single
        )
        if part.strip()
    ]
    if len(sentence_parts) > 1:
        return sentence_parts
    return [single.strip()] if single.strip() else []


def _has_explicit_bullet_marker_text(value: str) -> bool:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return False
    bullet_pattern = re.compile(
        r"(?:(?<=^)|(?<=\s)|(?<=\n))(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        flags=re.IGNORECASE,
    )
    return bool(bullet_pattern.search(normalized))


def _split_body_detail_units_max_two_sentences(value: str) -> list[str]:
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return []
    if _is_reference_like_body_line(normalized):
        return [normalized]
    if _has_inline_numeric_bullet_not_at_start(normalized):
        return [normalized]

    initial_units = _split_atomic_detail_units(normalized) or [normalized]
    final_units: list[str] = []
    for unit in initial_units:
        compact = _normalize_requirement_text(unit)
        if not compact:
            continue
        if re.match(
            r"^(?:[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
            compact,
            flags=re.IGNORECASE,
        ):
            final_units.append(compact)
            continue
        sentence_parts = [
            part.strip()
            for part in re.split(r"(?<![가-힣A-Za-z0-9]\.)(?<=[\.\!\?])\s+", compact)
            if part.strip()
        ]
        if len(sentence_parts) <= 2:
            final_units.append(compact)
            continue
        for idx in range(0, len(sentence_parts), 2):
            chunk = " ".join(sentence_parts[idx : idx + 2]).strip()
            if chunk:
                final_units.append(chunk)
    return final_units or ([normalized] if normalized else [])


def _split_table_cell_lines(value: str) -> list[str]:
    raw_value = str(value or "")
    if not raw_value:
        return []
    lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
    if not lines:
        compact = _normalize_requirement_text(raw_value)
        return [compact] if compact else []
    split_lines: list[str] = []
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if not normalized:
            continue
        split_lines.extend(_split_atomic_detail_units(normalized) or [normalized])
    return split_lines


def _split_three_col_detail_units(value: str) -> list[str]:
    normalized = _normalize_middle_dot_connectors(value)
    if not normalized:
        return []
    numbered_marker_units = _split_inline_numbered_marker_units(normalized)
    if len(numbered_marker_units) > 1:
        return numbered_marker_units
    repeated_bullet_units = _split_repeated_bullet_chain(normalized)
    if len(repeated_bullet_units) > 1:
        return repeated_bullet_units
    standalone_bullet_units = _split_inline_standalone_bullet_units(normalized)
    if len(standalone_bullet_units) > 1:
        return standalone_bullet_units
    if _has_inline_middle_dot_connector(normalized) and not re.match(
        r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return [normalized]
    if _is_reference_like_body_line(normalized):
        return [normalized]
    if _has_inline_numeric_bullet_not_at_start(normalized):
        return [normalized]

    if _has_explicit_bullet_marker_text(normalized):
        return _split_atomic_detail_units(normalized) or [normalized]

    line_parts = [
        _normalize_requirement_text(part)
        for part in normalized.split("\n")
        if _normalize_requirement_text(part)
    ]
    if len(line_parts) > 1:
        return line_parts

    compact = line_parts[0] if line_parts else normalized
    sentence_parts = [
        part.strip()
        for part in re.split(r"(?<=[\.\!\?])\s+(?=[A-Z0-9가-힣])", compact)
        if part.strip()
    ]
    return sentence_parts or ([compact] if compact else [])


def _split_single_column_table_lines(matrix: list[list[str]]) -> list[str]:
    lines: list[str] = []
    for row in matrix:
        for cell in row:
            cell_text = _cell_text_preserve_breaks(cell)
            for raw_line in str(cell_text or "").splitlines():
                normalized = _normalize_requirement_text(raw_line)
                if normalized:
                    lines.append(normalized)
    return lines


def _strip_single_column_table_marker(value: str) -> str:
    compact = _normalize_requirement_text(value)
    if not compact:
        return ""
    stripped = _strip_leading_numbering(compact)
    if stripped != compact:
        return stripped
    stripped = re.sub(r"^\s*[-•·▪■◆▶◦○□◇⦁\*]+\s+", "", compact).strip()
    return stripped or compact


def _extract_single_column_table_rows(
    card: RfpCard,
    matrix: list[list[str]],
    card_title: str,
    preferred_item_context: str,
    has_header_row: bool,
) -> list[dict]:
    item_name = _normalize_requirement_text(preferred_item_context)
    if not item_name:
        item_name = str(
            getattr(card, "sub_subject", None)
            or getattr(card, "subject", None)
            or card.requirement
            or ""
        ).strip()
    if not item_name:
        item_name = card_title

    lines = _split_single_column_table_lines(matrix)
    if not lines:
        return []

    if has_header_row:
        first_line = lines[0]
        if _is_header_like_field(first_line) or _normalize_requirement_text(
            first_line
        ) == _normalize_requirement_text(card_title):
            lines = lines[1:]

    rows: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    current_requirement = card_title
    emitted_requirement = ""

    def _emit_row(requirement_text: str, detail_text: str, build_method: str) -> None:
        requirement = _normalize_requirement_text(requirement_text) or card_title
        detail = _normalize_requirement_text(detail_text)
        if not detail:
            return
        detail_units = _split_atomic_detail_units(detail) or [detail]
        for detail_unit in detail_units:
            detail_unit = _normalize_requirement_text(detail_unit)
            if not detail_unit:
                continue
            key = ("", item_name, requirement, detail_unit)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": detail_unit,
                    "result_note": "",
                    "id_title_hint": "",
                    "build_method": build_method,
                }
            )

    for line in lines:
        bullet_level = _detect_bullet_level(line)
        line_body = _strip_single_column_table_marker(line)
        if bullet_level == 2 and line_body:
            current_requirement = line_body
            emitted_requirement = current_requirement
            _emit_row(current_requirement, line_body, "룰 기반(1단 표-레벨2)")
            continue

        requirement = emitted_requirement or current_requirement or card_title
        if bullet_level == 1 and not emitted_requirement:
            requirement = card_title
        _emit_row(requirement, line, "룰 기반(1단 표-불릿/줄)")

    return rows


def _leading_body_context_from_table_html(html_excerpt: str, fallback_title: str = "") -> str:
    blocks = _body_text_blocks(html_excerpt)
    if not blocks:
        return _normalize_requirement_text(fallback_title)

    title_norm = _normalize_requirement_text(fallback_title)
    leading_texts: list[str] = []
    for block in blocks:
        if block.get("tag") == "table":
            break
        text = _normalize_requirement_text(str(block.get("text") or ""))
        if not text:
            continue
        if title_norm and text == title_norm:
            continue
        leading_texts.append(text)

    if not leading_texts:
        return title_norm
    return _normalize_requirement_text("\n".join(leading_texts))


def _is_promotable_leading_table_heading(text: str, card_title: str = "") -> bool:
    normalized = _normalize_requirement_text(text)
    normalized_title = _normalize_requirement_text(card_title)
    if not normalized or normalized == normalized_title:
        return False
    if len(normalized) > 40:
        return False
    if re.match(r"^[○●◦•□▪■◆▶◇※]+\s*", normalized):
        return True
    return _is_heading_like_text(normalized)


def _normalize_two_col_table_item_name(context_text: str, fallback_title: str = "") -> str:
    def _strip_leading_numbering(text: str, *, preserve_korean_prefix: bool = False) -> str:
        normalized = _normalize_requirement_text(text)
        if not normalized:
            return ""
        if preserve_korean_prefix and re.match(
            r"^\s*(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]\s*",
            normalized,
            flags=re.IGNORECASE,
        ):
            return normalized
        return re.sub(
            r"^\s*(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*[\.\)]?|[IVXLCDM]+[\.\)]?|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]?)\s*",
            "",
            normalized,
            flags=re.IGNORECASE,
        ).strip()

    def _preserve_security_domain(source_text: str, resolved_text: str) -> str:
        normalized_source = _normalize_requirement_text(source_text)
        normalized_resolved = _normalize_requirement_text(resolved_text)
        if not normalized_source or not normalized_resolved:
            return normalized_resolved
        if (
            any(token in normalized_source for token in ["정보보호", "보안", "컴플라이언스"])
            and not any(
                token in normalized_resolved for token in ["정보보호", "보안", "컴플라이언스"]
            )
            and any(
                token in normalized_resolved
                for token in ["활용", "연계", "표준", "접근", "인증", "계정"]
            )
        ):
            return f"{normalized_resolved} 정보보호"
        return normalized_resolved

    def _finalize_item_name(text: str, *, preserve_korean_prefix: bool = False) -> str:
        normalized = _strip_leading_numbering(text, preserve_korean_prefix=preserve_korean_prefix)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"^[\)\]\}]+", "", normalized).strip()
        normalized = re.sub(r"\s+관련$", "", normalized).strip()
        normalized = re.sub(
            r"\s+규격\s*및(?:\s*(?:요구사항|요건|내용|세부\s*내용))?$", "", normalized
        ).strip()
        return re.sub(r"\s+(?:요청\s*사항|요구사항|요건|통제\s*요구사항)$", "", normalized).strip()

    def _prefer_source_when_resolution_too_generic(resolved_text: str, source_text: str) -> str:
        normalized_resolved = _normalize_requirement_text(resolved_text)
        normalized_source = _strip_leading_numbering(
            source_text, preserve_korean_prefix=preserve_korean_prefix
        )
        if not normalized_resolved:
            return normalized_source
        if not normalized_source:
            return normalized_resolved
        if _looks_like_broad_requirement_group_label(
            normalized_resolved, normalized_source, normalized_source
        ) and len(normalized_source) > len(normalized_resolved):
            return normalized_source
        return normalized_resolved

    preserve_korean_prefix = bool(
        re.match(
            r"^\s*(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]\s*",
            _normalize_requirement_text(context_text),
            flags=re.IGNORECASE,
        )
        or re.match(
            r"^\s*(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ])[\.\)]\s*",
            _normalize_requirement_text(fallback_title),
            flags=re.IGNORECASE,
        )
    )
    context = _strip_leading_numbering(context_text, preserve_korean_prefix=preserve_korean_prefix)
    title = _strip_leading_numbering(fallback_title, preserve_korean_prefix=preserve_korean_prefix)
    candidates = []
    if _is_item_name_like_text(context):
        candidates.append(context)
    if _is_item_name_like_text(title):
        candidates.append(title)
    patterns: list[tuple[str, callable]] = [
        (
            r"(.+?)\s+활용관련\s+정보보호\s+요구사항$",
            lambda m: f"{_normalize_requirement_text(m.group(1))} 활용",
        ),
        (
            r"(.+?)\s+기능그룹별\s+정보보호\s+요구사항$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
        (
            r"(.+?)\s+세부\s+정보보호\s+요건$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
        (
            r"(.+?)\s+관련\s+정보보호\s+요구사항$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
        (
            r"(.+?)\s+정보보호\s+요구사항$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
        (
            r"(.+?)\s+보안\s+요건$",
            lambda m: f"{_normalize_requirement_text(m.group(1))} 보안",
        ),
        (
            r"(.+?)\s+요청\s*사항$",
            lambda m: _normalize_requirement_text(m.group(1)),
        ),
    ]
    for text in candidates:
        if not text:
            continue
        for pattern, resolver in patterns:
            match = re.search(pattern, text)
            if match:
                resolved = _finalize_item_name(
                    resolver(match),
                    preserve_korean_prefix=preserve_korean_prefix,
                )
                resolved = _preserve_security_domain(text, resolved)
                if resolved:
                    return _prefer_source_when_resolution_too_generic(resolved, text)
    fallback_source = title or context
    fallback_resolved = _finalize_item_name(
        fallback_source, preserve_korean_prefix=preserve_korean_prefix
    )
    fallback_resolved = _prefer_source_when_resolution_too_generic(
        fallback_resolved, fallback_source
    )
    if not _is_item_name_like_text(context) and _is_item_name_like_text(title):
        return _preserve_security_domain(title, fallback_resolved)
    if _is_item_name_like_text(context):
        context_resolved = _finalize_item_name(
            context, preserve_korean_prefix=preserve_korean_prefix
        )
        context_resolved = _prefer_source_when_resolution_too_generic(context_resolved, context)
        return _preserve_security_domain(context, context_resolved)
    return _preserve_security_domain(fallback_source, fallback_resolved)


def _normalize_three_col_table_item_name(
    raw_item_name: str,
    context_text: str,
    fallback_title: str = "",
) -> str:
    normalized_item = _normalize_requirement_text(raw_item_name)
    compact = re.sub(r"[\s\-\_/,:;()\[\]{}]+", "", normalized_item).lower()
    generic_tokens = {
        "requirement",
        "requirements",
        "item",
        "itemname",
        "category",
        "group",
        "section",
        "subject",
        "title",
        "구분",
        "항목",
        "항목명",
        "요구사항",
        "상세",
        "상세요건",
        "내용",
    }
    if (
        normalized_item
        and not _is_header_like_field(normalized_item)
        and compact not in generic_tokens
    ):
        return normalized_item
    contextual_item_name = _normalize_two_col_table_item_name(context_text, fallback_title)
    return contextual_item_name or normalized_item


def _item_name_similarity_key(value: str) -> str:
    normalized = _normalize_requirement_text(value)
    normalized = re.sub(r"\s+", "", normalized)
    return re.sub(r"(?:정보보호|보안|컴플라이언스)$", "", normalized)


def _unify_similar_table_item_names(rows: list[dict]) -> None:
    if not rows:
        return

    variants: dict[str, list[str]] = {}
    for row in rows:
        item_name = _normalize_requirement_text(str(row.get("item_name") or ""))
        if not item_name:
            continue
        key = _item_name_similarity_key(item_name)
        if not key:
            continue
        variants.setdefault(key, [])
        if item_name not in variants[key]:
            variants[key].append(item_name)

    canonical_by_key: dict[str, str] = {}
    for key, names in variants.items():
        if not names:
            continue
        canonical = sorted(
            names,
            key=lambda name: (
                0 if any(token in name for token in ["정보보호", "보안", "컴플라이언스"]) else 1,
                -len(name),
                name,
            ),
        )[0]
        canonical_by_key[key] = canonical

    if not canonical_by_key:
        return

    for row in rows:
        item_name = _normalize_requirement_text(str(row.get("item_name") or ""))
        if not item_name:
            continue
        key = _item_name_similarity_key(item_name)
        canonical = canonical_by_key.get(key)
        if canonical:
            row["item_name"] = canonical


def _is_auxiliary_third_column_header(header_row: list[str]) -> bool:
    if len(header_row) < 3:
        return False
    third = _normalize_requirement_text(str(header_row[2] or ""))
    if not third:
        return False
    compact_third = re.sub(r"\s+", "", third).lower()
    patterns = [
        "비고",
        "비고사항",
        "참고사항",
        "관련 법규",
        "관련법규",
        "가이드라인",
        "내규",
        "참고",
        "참조",
        "근거",
        "법령",
        "규정",
        "수량",
        "수량(ea)",
        "수량(대)",
        "수량(개)",
        "수량(식)",
        "수량(set)",
        "ea",
        "단위",
        "수량/단위",
        "단위/수량",
        "수량 / 단위",
    ]
    compact_patterns = [re.sub(r"\s+", "", pattern).lower() for pattern in patterns]
    return any(pattern in compact_third for pattern in compact_patterns)


def _is_numeric_like_auxiliary_value(value: str) -> bool:
    compact = _normalize_requirement_text(value).strip().lower()
    if not compact:
        return False
    cleaned = re.sub(
        r"(ea|대|개|식|set|세트|box|박스|명|권|대수|수량|ea\b|set\b)$", "", compact
    ).strip()
    if re.search(r"[a-z가-힣]", cleaned):
        return False
    return bool(re.fullmatch(r"[\d,\.\-/()+%×xX\s]+", cleaned))


def _is_numeric_auxiliary_third_column(matrix: list[list[str]]) -> bool:
    if not matrix:
        return False
    max_cols = max(len(row) for row in matrix)
    if max_cols != 3:
        return False
    values = [
        _normalize_requirement_text(str(row[2] if len(row) > 2 else ""))
        for row in matrix[1:]
        if len(row) > 2
    ]
    non_empty = [value for value in values if value]
    if not non_empty:
        return False
    numeric_like = sum(1 for value in non_empty if _is_numeric_like_auxiliary_value(value))
    return numeric_like / max(len(non_empty), 1) >= 0.8


def _is_auxiliary_fourth_column_header(header_row: list[str]) -> bool:
    if len(header_row) < 4:
        return False
    fourth = _normalize_requirement_text(str(header_row[3] or ""))
    if not fourth:
        return False
    compact_fourth = re.sub(r"\s+", "", fourth).lower()
    patterns = [
        "비고",
        "비고사항",
        "참고사항",
        "참고",
        "추가정보",
        "비고란",
        "수량",
        "수량(ea)",
        "수량(대)",
        "수량(개)",
        "수량(식)",
        "수량(set)",
        "ea",
        "단위",
        "수량/단위",
        "단위/수량",
        "수량 / 단위",
    ]
    compact_patterns = [re.sub(r"\s+", "", pattern).lower() for pattern in patterns]
    return any(pattern in compact_fourth for pattern in compact_patterns)


def _is_grouped_three_level_header(header_row: list[str]) -> bool:
    if len(header_row) != 3:
        return False
    normalized = [_normalize_requirement_text(str(cell or "")) for cell in header_row]
    first, second, third = normalized
    if not third:
        return False
    if third in {"요구사항 상세", "상세", "상세내용", "요구사항", "요구 사항 상세"}:
        return first == second and first in {"구분", "분류", "항목"}
    return False


def _is_single_row_header_continuation_table(matrix: list[list[str]], has_header_row: bool) -> bool:
    if not has_header_row or len(matrix) != 1:
        return False
    row = list(matrix[0]) if matrix else []
    if len(row) not in {2, 3, 4}:
        return False
    normalized = [_normalize_requirement_text(str(cell or "")) for cell in row]
    non_empty = [cell for cell in normalized if cell]
    if len(non_empty) != 1:
        return False
    detail = non_empty[0]
    if len(detail) < 20:
        return False
    return any(
        token in detail
        for token in ("가.", "나.", "다.", "라.", "마.", "바.", "사.", "-", "•", "▪", "※")
    )


def _is_box_style_three_column_table(matrix: list[list[str]]) -> bool:
    if not matrix:
        return False
    if max(len(row) for row in matrix) != 3:
        return False
    has_content = False
    for row in matrix:
        padded = list(row) + [""] * max(0, 3 - len(row))
        first = _normalize_requirement_text(str(padded[0] or ""))
        second = _normalize_requirement_text(str(padded[1] or ""))
        third = _normalize_requirement_text(str(padded[2] or ""))
        if not third:
            return False
        if first or second:
            return False
        has_content = True
    return has_content


def _is_noisy_table_item_name(value: str) -> bool:
    raw_value = re.sub(r"\s+", " ", str(value or "")).strip()
    normalized = _normalize_requirement_text(value)
    if not normalized:
        return True
    if _is_header_like_field(normalized):
        return True
    return bool(re.match(r"^[\)\]\}\-–—]+", raw_value))


def _top_table_context_from_matrix(
    matrix: list[list[str]], fallback_title: str = ""
) -> tuple[str, int]:
    if not matrix:
        return "", 0

    title_norm = _normalize_requirement_text(fallback_title)
    for row_idx, row in enumerate(matrix[:2]):
        normalized_row = [_normalize_requirement_text(str(cell or "")) for cell in row]
        non_empty = [cell for cell in normalized_row if cell]
        unique_non_empty = []
        seen: set[str] = set()
        for cell in non_empty:
            if cell in seen:
                continue
            seen.add(cell)
            unique_non_empty.append(cell)
        if len(unique_non_empty) != 1:
            continue
        candidate = unique_non_empty[0]
        if title_norm and candidate == title_norm:
            continue
        if len(non_empty) < 2:
            continue
        return candidate, row_idx + 1
    return "", 0


def _split_heading_by_body_start_markers(
    text: str, markers: tuple[str, ...]
) -> tuple[str, str] | None:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return None
    marker_matches = [
        match.start()
        for marker in markers
        for match in [re.search(re.escape(marker), normalized)]
        if match and match.start() > 0
    ]
    if not marker_matches:
        return None
    split_idx = min(marker_matches)
    heading = _normalize_requirement_text(normalized[:split_idx])
    body = _normalize_requirement_text(normalized[split_idx:])
    if len(re.sub(r"\s+", "", heading)) <= 2:
        return None
    if heading and body:
        return heading, body
    return None


def _split_inline_heading_and_body(text: str) -> tuple[str, list[str]]:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return "", []

    special_heading_match = re.match(
        r"^(?P<heading>※\s*[^\.。\n]+?)\s+(?P<body>(?:본 사업은|해당 사업은|본 프로젝트는|"
        r"본 프로젝트의|본 프로젝트와 관련한|제안업체의|제안사의|수행사의|납품 솔루션은|"
        r"당사는|구축 완료 이후|추후 ).+)$",
        normalized,
    )
    if special_heading_match:
        return (
            _normalize_requirement_text(special_heading_match.group("heading")),
            [_normalize_requirement_text(special_heading_match.group("body"))],
        )

    split_result = _split_heading_by_body_start_markers(
        normalized, _COMMON_INLINE_BODY_START_MARKERS
    )
    if split_result:
        heading, body = split_result
        return heading, [body]
    return normalized, []


def _split_chained_numbered_heading_tail(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized or "\n" in normalized:
        return normalized
    heading_start_pattern = re.compile(
        r"(?<!\S)(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+){1,}\.?)\s*(?=\s+)",
        flags=re.IGNORECASE,
    )
    matches = list(heading_start_pattern.finditer(normalized))
    if len(matches) < 2:
        return normalized
    tail = _normalize_requirement_text(normalized[matches[1].start() :])
    return tail or normalized


def _split_inline_shared_root_lines(lines: list[str]) -> list[str]:
    expanded: list[str] = []
    inline_root_pattern = re.compile(
        r"^(?P<prefix>\s*(?:[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+|\(?\d+(?:\.\d+)*[\)\.]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.])\s+.+?)\s+(?P<marker>[○●◦•□▪■◆▶Oo])\s+(?P<rest>.+)$",
        flags=re.IGNORECASE,
    )
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if not normalized:
            continue
        normalized = _split_chained_numbered_heading_tail(normalized)
        match = inline_root_pattern.match(normalized)
        if match and _normalize_requirement_text(str(match.group("rest") or "")):
            prefix = _normalize_requirement_text(str(match.group("prefix") or ""))
            marker = _normalize_requirement_text(str(match.group("marker") or ""))
            rest = _normalize_requirement_text(str(match.group("rest") or ""))
            if prefix and marker and rest:
                expanded.append(prefix)
                expanded.append(f"{marker} {rest}".strip())
                continue
        expanded.append(normalized)
    return expanded


def _split_any_inline_korean_bullet_line(line: str) -> list[str]:
    normalized = _normalize_requirement_text(line)
    if not normalized:
        return []
    marker_pattern = re.compile(
        r"(?P<head>.+?)(?P<sep>\s+)(?P<marker>(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]|①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩)\s+(?P<tail>.+)",
        flags=re.IGNORECASE,
    )
    match = marker_pattern.search(normalized)
    if not match:
        return [normalized]
    head = _normalize_requirement_text(str(match.group("head") or ""))
    marker = _normalize_requirement_text(str(match.group("marker") or ""))
    tail = _normalize_requirement_text(str(match.group("tail") or ""))
    if not head or not marker or not tail:
        return [normalized]

    is_korean_letter = re.match(
        r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]",
        marker,
    )
    if is_korean_letter:
        if re.match(
            r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s*",
            head,
        ):
            return [head, f"{marker} {tail}".strip()]
        return [normalized]
    return [head, f"{marker} {tail}".strip()]


def _split_body_heading_and_detail_lines(text: str) -> tuple[str, list[str]]:
    normalized = _merge_quoted_numeric_reference_spans(text)
    if not normalized:
        return "", []

    numbered_heading_match = re.match(
        r"^(?P<prefix>(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?))\s+(?P<rest>.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if numbered_heading_match:
        prefix = _normalize_requirement_text(numbered_heading_match.group("prefix"))
        rest = _normalize_requirement_text(numbered_heading_match.group("rest"))
        split_result = _split_heading_by_body_start_markers(rest, _COMMON_INLINE_BODY_START_MARKERS)
        if split_result:
            heading_rest, body = split_result
            if (
                prefix
                and heading_rest
                and body
                and _is_title_like_requirement_text(f"{prefix} {heading_rest}".strip())
            ):
                return f"{prefix} {heading_rest}".strip(), [body]

    if normalized.startswith("※"):
        split_result = _split_heading_by_body_start_markers(
            normalized,
            _COMMON_INLINE_BODY_START_MARKERS,
        )
        if split_result:
            heading, body = split_result
            if heading and body:
                return heading, [body]

    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return "", []
    if len(lines) >= 2:
        first_line = _normalize_requirement_text(lines[0])
        second_line = _normalize_requirement_text(lines[1])
        if re.match(r"^[\-*·ㆍ−–—]+\s*.+$", first_line) and re.match(
            r"^M\s*-\s*\d+(?:\s*(?:개월|주|일|개월차|주차|월|년|단계|시점|전|후))?.+$",
            second_line,
            flags=re.IGNORECASE,
        ):
            merged_first = _normalize_requirement_text(f"{first_line} {second_line}")
            return merged_first, lines[2:]
        return lines[0], lines[1:]

    heading, inline_details = _split_inline_heading_and_body(lines[0])
    return heading, inline_details


def _split_numbered_heading_inline_body_lines(lines: list[str]) -> list[str]:
    expanded: list[str] = []
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if not normalized:
            continue
        heading, detail_lines = _split_body_heading_and_detail_lines(normalized)
        if (
            heading
            and detail_lines
            and heading != normalized
            and _is_title_like_requirement_text(heading)
        ):
            expanded.append(heading)
            expanded.extend(detail_lines)
            continue
        expanded.append(normalized)
    return expanded


def _split_rows_with_inline_numbered_heading(rows: list[dict]) -> list[dict]:
    expanded_rows: list[dict] = []
    for row in rows:
        detail_requirement = _normalize_requirement_text(str(row.get("detail_requirement") or ""))
        detail_requirement = _split_chained_numbered_heading_tail(detail_requirement)
        heading, detail_lines = _split_body_heading_and_detail_lines(detail_requirement)
        heading = _normalize_requirement_text(heading)
        detail_lines = [
            _normalize_requirement_text(line)
            for line in detail_lines
            if _normalize_requirement_text(line)
        ]
        if (
            heading
            and heading != detail_requirement
            and detail_lines
            and _is_title_like_requirement_text(heading)
        ):
            first_row = dict(row)
            first_row["detail_requirement"] = heading
            expanded_rows.append(first_row)

            second_row = dict(row)
            second_row["requirement"] = heading
            second_row["detail_requirement"] = _normalize_requirement_text("\n".join(detail_lines))
            expanded_rows.append(second_row)
            continue
        expanded_rows.append(row)
    return expanded_rows


def _split_inline_korean_letter_requirements(lines: list[str]) -> list[str]:
    expanded: list[str] = []
    korean_letter_marker = (
        r"(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)"
    )
    pattern = re.compile(rf"^\s*({korean_letter_marker})[\.\)]\s+")
    for line in lines:
        normalized = _normalize_middle_dot_connectors(line)
        if not normalized:
            continue
        if _has_inline_middle_dot_connector(normalized) and not re.match(
            r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
            normalized,
            flags=re.IGNORECASE,
        ):
            expanded.append(normalized)
            continue
        if _looks_like_sentence_text(normalized) and not re.match(
            r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
            normalized,
            flags=re.IGNORECASE,
        ):
            expanded.append(normalized)
            continue

        leading_dash_match = re.match(
            rf"^(?P<head>[\-*·ㆍ−–—]+\s*.+?)(?P<marker>(?:{korean_letter_marker})[\.\)]\s+.+)$",
            normalized,
        )
        if leading_dash_match:
            head = _normalize_requirement_text(leading_dash_match.group("head"))
            marker_tail = _normalize_requirement_text(leading_dash_match.group("marker"))
            if head and marker_tail:
                expanded.append(head)
                expanded.extend(_split_inline_korean_letter_requirements([marker_tail]))
                continue

        match = pattern.match(normalized)
        if not match:
            expanded.append(normalized)
            continue
        expanded.append(normalized)
    return expanded


def _is_brief_heading_phrase(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    if re.match(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+\s*", normalized):
        return False
    if _looks_like_sentence_text(normalized):
        return False
    stripped_numbering = (
        _strip_leading_numbering(normalized) if _has_leading_numbering(normalized) else normalized
    )
    compact = _normalize_requirement_text(stripped_numbering)
    if not compact:
        return False
    return len(compact) <= 40


def _should_skip_llm_for_heading_only_body_card(plain_text: str, card_title: str = "") -> bool:
    normalized = _normalize_requirement_text(plain_text)
    if not normalized:
        return True

    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    normalized_title = _normalize_requirement_text(card_title)
    if normalized_title and lines and _normalize_requirement_text(lines[0]) == normalized_title:
        lines = lines[1:]
    if not lines:
        return True

    # If the remaining body is just a short list of heading phrases without
    # bullets or sentence-like details, LLM tends to invent explanatory prose.
    return all(_is_brief_heading_phrase(line) for line in lines)


def _body_line_raw_level(text: str) -> int | None:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return None
    if _is_reference_like_body_line(normalized):
        return None
    if re.match(r"^[Oo]\s+", normalized):
        return 1
    if re.match(
        r"^(?:제?\s*\d+\s*(?:장|절|항)|[IVXLCDM]+[\.\)]|\d+(?:\.\d+)+)\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return 1
    if re.match(r"^[\-*·ㆍ−–—]+\s*", normalized):
        return 2
    if re.match(
        r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s*",
        normalized,
    ):
        return 3
    if re.match(r"^(?:\(?\d+\)|\d+[\.\)])\s*", normalized):
        return 4
    if re.match(r"^(?:[A-Za-z][\.\)]|[•▪■◆▶◇□※⦁◦○])\s*", normalized, flags=re.IGNORECASE):
        return 5
    return None


def _bullet_family(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return "other"
    if re.match(r"^[Oo]\s+", normalized):
        return "symbol"
    if re.match(
        r"^(?:제?\s*\d+\s*(?:장|절|항)|[IVXLCDM]+[\.\)]|\d+(?:\.\d+)+)\s*",
        normalized,
        flags=re.IGNORECASE,
    ):
        return "heading"
    if re.match(r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하)[\.\)]\s*", normalized):
        return "hangul_syllable"
    if re.match(r"^[ㄱ-ㅎ][\.\)]\s*", normalized):
        return "hangul_jamo"
    if re.match(r"^\d+\.\s*", normalized):
        return "digit_dot"
    if re.match(r"^\(?\d+\)\s*", normalized):
        return "digit_paren"
    if re.match(r"^(?:[A-Za-z]\.\s+|[A-Za-z]\)\s+)", normalized):
        return "latin"
    if re.match(r"^[•▪■◆▶◇□※⦁◦○]\s*", normalized):
        return "symbol"
    if re.match(r"^[\-*·ㆍ−–—]+\s*", normalized):
        return "dash"
    return "other"


def _bullet_level_from_family(family: str) -> int:
    return {
        "heading": 1,
        "hangul_syllable": 2,
        "dash_mid": 3,
        "hangul_jamo": 4,
        "digit_dot": 5,
        "digit_paren": 6,
        "latin": 7,
        "symbol": 8,
        "dash_low": 9,
        "other": 10,
    }.get(family, 10)


def _profiled_bullet_family(text: str, *, next_text: str = "") -> str:
    family = _bullet_family(text)
    if family != "dash":
        return family
    next_family = _bullet_family(next_text)
    if next_family in {"hangul_jamo", "digit_dot", "digit_paren", "latin", "symbol"}:
        return "dash_mid"
    return "dash_low"


def _infer_section_body_hierarchy_profile(section_name: str, cards: list[RfpCard]) -> dict:
    family_sequences: list[list[str]] = []
    for card in cards:
        lines = _html_excerpt_lines(str(getattr(card, "html_excerpt", "") or ""))
        if not lines:
            continue
        card_title = _normalize_requirement_text(
            str(getattr(card, "subject", None) or getattr(card, "requirement", None) or "")
        )
        if card_title and _normalize_requirement_text(lines[0]) == card_title:
            lines = lines[1:]
        lines = _merge_bullet_continuation_lines(lines)
        lines = _strip_da_marker_when_no_ga_na(lines)
        lines = _merge_plain_sentence_wraps(lines)
        lines = _split_inline_shared_root_lines(lines)
        sequence: list[str] = []
        for idx, line in enumerate(lines):
            normalized_line = _normalize_requirement_text(line)
            if (
                not normalized_line
                or _is_section_reference_note_line(normalized_line)
                or _is_reference_like_body_line(normalized_line)
            ):
                continue
            next_text = _normalize_requirement_text(lines[idx + 1]) if idx + 1 < len(lines) else ""
            family = _profiled_bullet_family(normalized_line, next_text=next_text)
            if family == "other":
                continue
            if not sequence or sequence[-1] != family:
                sequence.append(family)
        if sequence:
            family_sequences.append(sequence)

    normalized_section = _normalize_requirement_text(section_name)
    if not family_sequences:
        return {
            "family_sequence": [],
            "family_level_map": {},
            "root_family": "",
            "requirement_family": "",
            "detail_family": "",
            "uses_shared_parent_root": False,
            "uses_dash_item_root": False,
            "no_hierarchy": True,
            "section_name": normalized_section,
        }

    ordered_families: list[str] = []
    max_depth = max(len(seq) for seq in family_sequences)
    for position in range(max_depth):
        position_counts = Counter(
            seq[position] for seq in family_sequences if position < len(seq) and seq[position]
        )
        for family, _ in position_counts.most_common():
            if family not in ordered_families:
                ordered_families.append(family)
                break

    if not ordered_families:
        ordered_families = [family_sequences[0][0]]

    family_level_map = {family: idx + 1 for idx, family in enumerate(ordered_families)}
    root_family = ordered_families[0] if ordered_families else ""
    requirement_family = ordered_families[1] if len(ordered_families) > 1 else ""
    detail_family = ordered_families[2] if len(ordered_families) > 2 else ""

    return {
        "family_sequence": ordered_families,
        "family_level_map": family_level_map,
        "root_family": root_family,
        "requirement_family": requirement_family,
        "detail_family": detail_family,
        "uses_shared_parent_root": root_family == "symbol",
        "uses_dash_item_root": root_family in {"dash_mid", "dash_low"},
        "no_hierarchy": False,
        "section_name": normalized_section,
    }


def _profiled_bullet_level_from_family(
    family: str, section_profile: dict | None = None, *, next_text: str = ""
) -> int:
    profile = section_profile or {}
    level_map = profile.get("family_level_map") or {}
    normalized_family = family
    if normalized_family == "dash":
        next_family = _profiled_bullet_family(next_text)
        if next_family in {"hangul_jamo", "digit_dot", "digit_paren", "latin", "symbol"}:
            normalized_family = "dash_mid"
        else:
            normalized_family = "dash_low"
    if normalized_family in level_map:
        return int(level_map.get(normalized_family) or 10)
    if normalized_family == "symbol" and profile.get("root_family") == "symbol":
        return 1
    return _bullet_level_from_family(normalized_family)


def _leading_bullet_prefix(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    first_line = next((line.strip() for line in normalized.split("\n") if line.strip()), "")
    if not first_line:
        return ""
    match = re.match(r"^(?P<prefix>[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+)\s*(?P<body>.+)$", first_line)
    if match and _normalize_requirement_text(str(match.group("body") or "")):
        return str(match.group("prefix") or "").strip()
    return ""


def _infer_dash_mid_item_name_from_text(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 2:
        return ""
    first_dash_index = next(
        (
            idx
            for idx, line in enumerate(lines)
            if re.match(r"^[\-*·ㆍ−–—]+\s*", _normalize_requirement_text(line))
        ),
        None,
    )
    if first_dash_index is None or first_dash_index + 1 >= len(lines):
        return ""
    next_line = _normalize_requirement_text(lines[first_dash_index + 1])
    if not re.match(r"^ㄱ[\.\)]\s*", next_line):
        return ""
    return _normalize_requirement_text(lines[first_dash_index])


def _extract_atomic_body_rows(
    lines: list[str],
    *,
    title: str = "",
    default_item_name: str = "",
    build_method: str = "룰 기반(본문 계층 정규화)",
) -> list[dict]:
    normalized_title = _normalize_requirement_text(title)
    default_level1 = _normalize_requirement_text(default_item_name) or normalized_title
    if not default_level1:
        return []
    base_requirement = normalized_title or default_level1
    rows: list[dict] = []
    for line in lines:
        normalized_line = _normalize_requirement_text(line)
        if not normalized_line or _is_only_page_info(normalized_line):
            continue
        if normalized_title and normalized_line == normalized_title:
            continue
        rows.append(
            {
                "item_name": default_level1,
                "requirement": base_requirement,
                "detail_requirement": normalized_line,
                "result_note": "",
                "build_method": build_method,
                "build_source_detail": "계층 없음 / atomic",
            }
        )
    return rows


def _infer_korean_heading_before_dash_detail(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) < 2:
        return ""
    heading_pattern = re.compile(
        r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s+"
    )
    dash_pattern = re.compile(r"^[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+\s*")
    for idx, line in enumerate(lines[:-1]):
        if not heading_pattern.match(line):
            continue
        next_line = lines[idx + 1]
        if dash_pattern.match(next_line) or _has_inline_middle_dot_connector(next_line):
            return _normalize_requirement_text(line)
    return ""


def _should_override_requirement_with_korean_heading(build_source: str) -> bool:
    return _normalize_requirement_text(build_source) == "본문"


def _extract_repeated_dash_item_rows(
    lines: list[str], *, title: str = "", default_item_name: str = "", build_method: str = "룰 기반"
) -> list[dict]:
    normalized_lines = [
        _normalize_requirement_text(line) for line in lines if _normalize_requirement_text(line)
    ]
    if not normalized_lines:
        return []

    normalized_title = _normalize_requirement_text(title)
    work_lines = normalized_lines[:]
    if (
        normalized_title
        and work_lines
        and _normalize_requirement_text(work_lines[0]) == normalized_title
    ):
        work_lines = work_lines[1:]
    if len(work_lines) < 2:
        return []

    korean_req_pattern = re.compile(r"^[ㄱ-ㅎ][\.\)]\s*")

    dash_blocks: list[int] = []
    for idx, line in enumerate(work_lines):
        if not re.match(r"^[\-*·ㆍ−–—]+\s*.+$", line):
            continue
        if idx + 1 >= len(work_lines) or not korean_req_pattern.match(
            _normalize_requirement_text(work_lines[idx + 1])
        ):
            continue
        dash_blocks.append(idx)

    if not dash_blocks:
        return []

    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    prelude_rows: list[dict] = []
    prelude_seen: set[tuple[str, str, str]] = set()
    first_dash_block_idx = dash_blocks[0]
    for line in work_lines[:first_dash_block_idx]:
        if not re.match(r"^[\-*·ㆍ−–—]+\s*.+$", line):
            continue
        detail_text = _normalize_requirement_text(line)
        if not detail_text or _is_only_page_info(detail_text):
            continue
        key = (
            _normalize_requirement_text(default_item_name)
            or normalized_title
            or _normalize_requirement_text(work_lines[0]),
            normalized_title
            or _normalize_requirement_text(default_item_name)
            or _normalize_requirement_text(work_lines[0]),
            detail_text,
        )
        if key in prelude_seen:
            continue
        prelude_seen.add(key)
        prelude_rows.append(
            {
                "item_name": _normalize_requirement_text(default_item_name)
                or normalized_title
                or _normalize_requirement_text(work_lines[0]),
                "requirement": normalized_title
                or _normalize_requirement_text(default_item_name)
                or _normalize_requirement_text(work_lines[0]),
                "detail_requirement": detail_text,
                "result_note": "",
                "build_method": build_method,
                "build_source_detail": "계층 없음 / atomic",
            }
        )
    for block_idx, item_idx in enumerate(dash_blocks):
        block_end = (
            dash_blocks[block_idx + 1] if block_idx + 1 < len(dash_blocks) else len(work_lines)
        )
        item_name = _normalize_requirement_text(work_lines[item_idx])
        requirement_indices = [
            idx
            for idx in range(item_idx + 1, block_end)
            if korean_req_pattern.match(_normalize_requirement_text(work_lines[idx]))
        ]
        if not requirement_indices:
            continue
        for req_pos, req_idx in enumerate(requirement_indices):
            next_req_idx = (
                requirement_indices[req_pos + 1]
                if req_pos + 1 < len(requirement_indices)
                else block_end
            )
            requirement_text = _normalize_requirement_text(work_lines[req_idx])
            detail_lines = work_lines[req_idx + 1 : next_req_idx]
            if re.fullmatch(r"^[ㄱ-ㅎ][\.\)]", requirement_text) and detail_lines:
                first_detail_line = _normalize_requirement_text(detail_lines[0])
                if first_detail_line and not re.match(r"^[ㄱ-ㅎ][\.\)]\s*", first_detail_line):
                    requirement_text = _normalize_requirement_text(
                        f"{requirement_text} {first_detail_line}"
                    )
                    detail_lines = detail_lines[1:]
            detail_units: list[str] = []
            for line in detail_lines:
                detail_units.extend(_split_atomic_detail_units(line) or [line])
            if not detail_units:
                detail_units = [requirement_text]
            for detail_unit in detail_units:
                cleaned = _strip_trailing_orphan_bullet(_normalize_requirement_text(detail_unit))
                if not cleaned or _is_only_page_info(cleaned):
                    continue
                cleaned = _normalize_requirement_text(cleaned)
                key = (item_name, requirement_text, cleaned)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "item_name": item_name,
                        "requirement": requirement_text,
                        "detail_requirement": cleaned,
                        "result_note": "",
                        "build_method": build_method,
                        "build_source_detail": "항목명, 요구사항, 상세요건",
                    }
                )
    return prelude_rows + rows


def _is_numbered_body_hierarchy_line(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    if _is_reference_like_body_line(normalized):
        return False
    return bool(
        re.match(
            r"^(?:제?\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s*", normalized, flags=re.IGNORECASE
        )
        or re.match(
            r"^(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s*",
            normalized,
        )
        or re.match(r"^(?:\(?\d+\)|\d+[\.\)])\s*", normalized)
        or re.match(r"^[\-*•▪■◆▶◦○□◇·ㆍ⦁−–—※]+\s*", normalized)
    )


def _is_only_page_info(text: str) -> bool:
    if not text:
        return False
    val = text.strip()
    val = re.sub(r"^[•▪■◆▶◦○□◇·ㆍ−–—\-\*\s]+", "", val)
    val = val.strip().lower()
    num_part = r"(?:of|[\d\s\-\~\,\.\(\)\/\+\&])+"
    patterns = [
        rf"^p\.?\s*{num_part}$",
        rf"^page\s*{num_part}$",
        rf"^{num_part}p$",
        rf"^{num_part}페이지$",
        rf"^페이지\s*{num_part}$",
        r"^\d+$",
        r"^\d+\s*[\-\~]\s*\d+$",
    ]
    combined = "|".join(patterns)
    return bool(re.match(combined, val))


def _is_section_reference_note_line(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    if not re.match(r"^[※*]\s*\S+", normalized):
        return False
    compact = re.sub(r"\s+", "", normalized)
    note_hints = [
        "사업내용이변경될경우",
        "과업변경절차",
        "상기요구사항",
        "제안범위에포함",
        "추가제안가능",
        "본RFP에서제시한기능",
        "대체가능한기능",
        "구현방안을포함",
        "필수적이라판단되는사항",
    ]
    return any(hint in compact for hint in note_hints) or len(compact) >= 25


def _is_reference_like_body_line(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    if _is_section_reference_note_line(normalized):
        return True
    if re.search(r"(?:참고|참조)하시기바랍니다?", compact):
        return True
    if re.search(r"(?:을|를)참고(?:하시기바랍니다?)?", compact):
        return True
    if re.search(r"별첨\d+(?:\.\d+)*", compact):
        return True
    return bool(re.search(r"『\d+(?:\.\d+)+[^』]*』?\s*을?참고", compact))


def _has_inline_numeric_bullet_not_at_start(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    return bool(
        re.search(
            r"(?<!^)(?:^|\s)(?:\(?\d+(?:\.\d+)*[\)\.\-])\s+",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _is_lonely_korean_bullet_marker_line(text: str) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return False
    return bool(
        re.fullmatch(
            r"(?:가|나|다|라|마|바|사|아|자|차|카|타|파|하|ㄱ|ㄴ|ㄷ|ㄹ|ㅁ|ㅂ|ㅅ|ㅇ|ㅈ|ㅊ|ㅋ|ㅌ|ㅍ|ㅎ)[\.\)]\s*",
            normalized,
        )
    )


def _strip_da_marker_when_no_ga_na(lines: list[str]) -> list[str]:
    has_ga_na = any(
        re.match(r"^(?:가|나)[\.\)]\s*", _normalize_requirement_text(line)) for line in lines
    )
    if has_ga_na:
        return lines
    # 한 줄짜리 "다."는 본문 머리표일 수 있으니 그대로 둔다.
    if len(lines) <= 1:
        return lines
    adjusted: list[str] = []
    for line in lines:
        normalized = _normalize_requirement_text(line)
        if re.match(r"^다[\.\)]\s+", normalized):
            normalized = re.sub(r"^다[\.\)]\s*", "", normalized)
        adjusted.append(normalized)
    return adjusted


def _merge_plain_sentence_wraps(lines: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    while idx < len(lines):
        line = _normalize_requirement_text(lines[idx])
        if not line:
            idx += 1
            continue
        if not merged:
            merged.append(line)
            idx += 1
            continue

        prev = merged[-1]
        current_has_marker = bool(
            re.match(
                r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
                line,
                flags=re.IGNORECASE,
            )
        )
        prev_has_marker = bool(
            re.match(
                r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
                prev,
                flags=re.IGNORECASE,
            )
        )
        prev_looks_open = bool(re.search(r"[가-힣A-Za-z0-9]$", prev)) and not re.search(
            r"[\.!?。:：]$", prev
        )

        if not prev_has_marker and not current_has_marker and prev_looks_open:
            merged[-1] = f"{prev} {line}".strip()
            idx += 1
            continue
        merged.append(line)
        idx += 1
    return merged


def _merge_bullet_continuation_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    while idx < len(lines):
        raw_line = lines[idx]
        line = _normalize_requirement_text(raw_line)
        if not line:
            idx += 1
            continue
        if not merged:
            merged.append(line)
            idx += 1
            continue

        prev = merged[-1]
        prev_is_heading_like = _is_heading_like_text(prev) or _is_title_like_requirement_text(prev)
        line_looks_like_sentence = _looks_like_sentence_text(line)
        prev_has_marker = bool(
            re.match(
                r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
                prev,
                flags=re.IGNORECASE,
            )
        )
        line_is_schedule_marker = bool(
            re.match(
                r"^\s*[A-Z]\s*-\s*\d+(?:\s*(?:개월|주|일|개월차|주차|월|년|단계|시점|전|후))?\b",
                line,
            )
        )
        line_has_marker = bool(
            re.match(
                r"^\s*(?:[•▪■◆▶◦○□◇·ㆍ−–—⦁\-\*]+|\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[ㄱ-ㅎ]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s*",
                line,
                flags=re.IGNORECASE,
            )
        )
        if line_is_schedule_marker:
            line_has_marker = False
        line_is_note = _is_section_reference_note_line(line) or _is_reference_like_body_line(line)
        prev_looks_open = bool(re.search(r"[가-힣A-Za-z0-9]$", prev)) and not re.search(
            r"[\.!?。:：]$", prev
        )

        # Keep a heading line and its following sentence as separate units.
        # Otherwise HTML heading blocks like `<h4>1.1. 프로젝트 개요</h4><p>...</p>`
        # can collapse into one line, and quoted references such as `『1.2. ...』`
        # may later be misread as inline numbered markers.
        if prev_is_heading_like and line_looks_like_sentence:
            merged.append(line)
            idx += 1
            continue

        if _is_lonely_korean_bullet_marker_line(line):
            if prev_looks_open:
                merged[-1] = f"{prev} {line}".strip()
                idx += 1
                continue
            next_line = _normalize_requirement_text(lines[idx + 1]) if idx + 1 < len(lines) else ""
            if (
                next_line
                and not _is_lonely_korean_bullet_marker_line(next_line)
                and not line_has_marker
            ):
                merged.append(f"{line} {next_line}".strip())
                idx += 2
                continue
        if prev_has_marker and not line_has_marker and not line_is_note and prev_looks_open:
            merged[-1] = f"{prev} {line}".strip()
            idx += 1
            continue
        merged.append(line)
        idx += 1
    return merged


def _extract_hierarchical_body_rows(
    plain_text: str,
    title: str = "",
    default_item_name: str = "",
    section_context: dict | None = None,
) -> list[dict]:
    normalized = _normalize_requirement_text(plain_text)
    if not normalized:
        return []
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return []
    default_level1 = (
        _normalize_requirement_text(default_item_name)
        or _normalize_requirement_text(title)
        or lines[0]
    )
    return _extract_atomic_body_rows(
        lines,
        title=title,
        default_item_name=default_level1,
        build_method="룰 기반(단일 행 본문)",
    )


def _extract_shared_parent_lettered_body_rows(
    plain_text: str,
    title: str = "",
    section_context: dict | None = None,
) -> list[dict]:
    normalized = _normalize_requirement_text(plain_text)
    if not normalized:
        return []
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines:
        return []
    default_level1 = _normalize_requirement_text(
        (section_context or {}).get("default_item_name") or title or lines[0]
    )
    return _extract_atomic_body_rows(
        lines,
        title=title,
        default_item_name=default_level1,
        build_method="룰 기반(단일 행 본문)",
    )


def _section_requirement_prefix(section_name: str) -> str:
    compact = re.sub(r"\s+", " ", (section_name or "")).strip()
    compact = re.sub(r"^[\-\*•·▪■◆▶◦○□◇\(\)\[\]]+\s*", "", compact)
    compact = re.sub(
        r"^(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?|[IVXLCDM]+[\.\)]?)\s*",
        "",
        compact,
        flags=re.IGNORECASE,
    )
    compact = re.sub(r"[\s/]+", "", compact)
    compact = re.sub(r"[^0-9A-Za-z가-힣]+", "", compact)
    return (compact or "요구사항")[:12]


def _clean_item_name_for_id(item_name: str) -> str:
    cleaned = _normalize_requirement_text(item_name)
    cleaned = re.sub(r"^[○●◦•□▪■◆▶]+\s*", "", cleaned).strip()
    cleaned = re.sub(r"^[Oo]\s+(?=\S)", "", cleaned).strip()
    cleaned = re.sub(
        r"^\s*(?:\(?\d+(?:\.\d+)*[\)\.\-]|(?:[가나다라마바사아자차카타파하]|[A-Za-z])[\)\.\-]|[IVXLCDM]+[\)\.\-])\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"\b(?:등|등의|등을|등은|등에|등으로|등과 같은)\b.*$", "", cleaned).strip()
    return re.sub(r"(?:입니다|한다|해야 함|하여야 함|필요|지원|제공)$", "", cleaned).strip()


def _extract_domain_prefix(text: str) -> str:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return ""
    for token in ["UX/UI", "UI/UX", "UX", "UI", "RAG", "ICT", "AI", "MCP"]:
        if token in normalized:
            return token
    paren_match = re.search(r"\(([^()]{1,20})\)", normalized)
    if paren_match:
        inner = _normalize_requirement_text(paren_match.group(1))
        inner = re.sub(r"\s+", "", inner)
        if inner:
            return inner
    return ""


def _looks_like_broad_requirement_group_label(
    text: str, category: str = "", section_name: str = ""
) -> bool:
    normalized = _normalize_requirement_text(text)
    if not normalized:
        return True
    normalized_category = _normalize_requirement_text(category)
    normalized_section = _normalize_requirement_text(section_name)
    if normalized in {normalized_category, normalized_section}:
        return True
    if re.match(r"^\s*(?:제\s*\d+\s*(?:장|절|항)|\d+(?:\.\d+)*\.?)\s*", normalized):
        return True
    broad_tokens = [
        "제안",
        "제안요청 개요",
        "상세 요구사항",
        "상세요구사항",
        "요구사항",
        "제안 요구사항",
        "구축 사업",
        "제안의 평가",
        "정보분석계",
        "프로젝트 범위",
        "사업 개요",
    ]
    return any(token in normalized for token in broad_tokens)


def _normalize_table_item_name_with_card_title(
    item_name: str,
    *,
    card_title: str,
    default_item_name: str = "",
    card_requirement: str = "",
    section_title: str = "",
    part_text: str = "",
) -> str:
    normalized_item = _normalize_requirement_text(item_name)
    normalized_card_title = _normalize_requirement_text(card_title)
    normalized_default = _normalize_requirement_text(default_item_name)
    normalized_requirement = _normalize_requirement_text(card_requirement)
    normalized_section = _normalize_requirement_text(section_title)
    normalized_part = _normalize_requirement_text(part_text)

    fallback_title = normalized_default or normalized_card_title
    if not fallback_title:
        return normalized_item

    broad_candidates = {
        value
        for value in (
            normalized_requirement,
            normalized_section,
            normalized_part,
        )
        if value
    }
    if not normalized_item:
        return fallback_title
    if normalized_item in broad_candidates and normalized_item != fallback_title:
        return fallback_title
    if (
        _looks_like_broad_requirement_group_label(
            normalized_item, normalized_requirement, normalized_section
        )
        and fallback_title
        and fallback_title != normalized_item
    ):
        return fallback_title
    return normalized_item


def _select_requirement_id_source(
    item_name: str,
    requirement: str,
    category: str,
    section_name: str,
    id_prefix_hint: str = "",
    id_title_hint: str = "",
) -> str:
    explicit_hint = _normalize_requirement_text(id_prefix_hint or id_title_hint)
    if explicit_hint:
        return explicit_hint

    normalized_item = _normalize_requirement_text(item_name)
    normalized_requirement = _normalize_requirement_text(requirement)

    if normalized_item == "공통":
        return normalized_item

    item_broad = _looks_like_broad_requirement_group_label(normalized_item, category, section_name)
    requirement_broad = _looks_like_broad_requirement_group_label(
        normalized_requirement, category, section_name
    )

    # If item_name is a broad section/card label and requirement is the actual subgroup,
    # use requirement as the grouping source so rows under the same subgroup share one ID family.
    if item_broad and normalized_requirement and not requirement_broad:
        return normalized_requirement

    # If item_name and requirement are identical, keep one representative source.
    if normalized_item and normalized_item == normalized_requirement:
        return normalized_item

    if normalized_item and not item_broad:
        return normalized_item
    if normalized_requirement:
        return normalized_requirement
    return (
        normalized_item
        or normalized_requirement
        or _normalize_requirement_text(category)
        or _normalize_requirement_text(section_name)
    )


def _row_requirement_id_prefix(
    item_name: str, category: str, section_name: str, fallback_prefix: str
) -> str:
    cleaned_item_name = _clean_item_name_for_id(item_name)
    if not cleaned_item_name:
        return fallback_prefix

    if cleaned_item_name == "공통":
        domain = _extract_domain_prefix(category) or _extract_domain_prefix(section_name)
        if domain:
            return f"{domain}공통"
        return "공통"

    domain = _extract_domain_prefix(cleaned_item_name)
    if domain and domain != cleaned_item_name:
        return domain

    # 문장 수준으로 길 경우 핵심 단어들만 룰 기반으로 추출하여 ID 프리픽스 가독성 및 정제 효율 개선
    words = [w for w in cleaned_item_name.split() if w.strip()]
    if len(words) >= 2:
        filter_words = []
        for w in words[:2]:
            cleaned_w = re.sub(
                r"(?:관련|위한|대한|구축|개발|수행|요구사항|기준|대상|내용|제공|지원|시스템)$",
                "",
                w,
            )
            if cleaned_w:
                filter_words.append(cleaned_w)
        compact = "".join(filter_words) if filter_words else "".join(words[:2])
    else:
        compact = cleaned_item_name

    compact = re.sub(r"[^0-9A-Za-z가-힣/\s]+", "", compact).strip()
    compact = re.sub(r"\s+", " ", compact)

    # Do not cut through a word boundary for Latin-word labels such as
    # "Object Storage" -> "ObjectStorage", not "ObjectSt".
    word_tokens = [token for token in compact.split(" ") if token]
    if len(word_tokens) >= 2:
        joined = "".join(word_tokens)
        if len(joined) <= 20:
            return joined
        kept: list[str] = []
        current_len = 0
        for token in word_tokens:
            token_len = len(token)
            if kept and current_len + token_len > 20:
                break
            if not kept and token_len > 20:
                return token[:20]
            kept.append(token)
            current_len += token_len
        if kept:
            return "".join(kept)

    compact_no_space = re.sub(r"\s+", "", compact)
    return (compact_no_space or fallback_prefix)[:20]


def _group_cards_by_section(cards: list[RfpCard]) -> list[tuple[str, list[RfpCard]]]:
    grouped: dict[str, list[RfpCard]] = {}
    order: list[str] = []
    for card in cards:
        group_name = str(getattr(card, "part", "") or "").strip()
        section_name = str(getattr(card, "section", "") or "").strip()
        requirement_name = str(getattr(card, "requirement", "") or "").strip()
        if group_name and section_name and group_name != section_name:
            section_label = f"{section_name} < {group_name}"
        else:
            section_label = section_name or group_name or requirement_name or "기타"
        if section_label not in grouped:
            grouped[section_label] = []
            order.append(section_label)
        grouped[section_label].append(card)
    return [(name, grouped[name]) for name in order]


def _extract_rows_from_table_card(
    card: RfpCard,
    two_col_item_name_override: str = "",
    section_context: dict | None = None,
) -> list[dict]:
    html_excerpt = str(card.html_excerpt or "").strip()
    if "<table" not in html_excerpt.lower():
        return []

    def _extract_leading_body_rows_before_first_table() -> list[dict]:
        blocks = _body_text_blocks(html_excerpt)
        if not blocks:
            return []
        leading_blocks: list[dict] = []
        for block in blocks:
            if block.get("tag") == "table":
                break
            leading_blocks.append(block)
        if not leading_blocks:
            return []
        leading_html = "\n".join(str(block.get("html") or "") for block in leading_blocks).strip()
        if not leading_html:
            return []
        leading_plain = _plain_text_from_html_excerpt(leading_html)
        if not leading_plain:
            return []
        default_item_name = str(getattr(card, "subject", None) or card.requirement or "").strip()
        hierarchical_rows = _extract_hierarchical_body_rows(
            leading_plain,
            title=card_title,
            default_item_name=default_item_name,
            section_context=section_context,
        )
        shared_parent_rows = _extract_shared_parent_lettered_body_rows(
            leading_plain,
            title=card_title,
            section_context=section_context,
        )
        body_rows = shared_parent_rows if len(shared_parent_rows) >= 2 else hierarchical_rows
        normalized_rows: list[dict] = []
        for row in body_rows:
            item_name = _normalize_requirement_text(str(row.get("item_name") or ""))
            requirement = _normalize_requirement_text(str(row.get("requirement") or ""))
            detail_requirement = _normalize_requirement_text(
                str(row.get("detail_requirement") or "")
            )
            result_note = _normalize_requirement_text(str(row.get("result_note") or ""))
            if not item_name or not requirement or not detail_requirement:
                continue
            normalized_rows.append(
                {
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": detail_requirement,
                    "result_note": result_note,
                    "id_title_hint": "",
                    "build_method": "룰 기반(표전 본문)",
                }
            )
        if len(normalized_rows) == 1:
            only_row = normalized_rows[0]
            only_item_name = _normalize_requirement_text(str(only_row.get("item_name") or ""))
            only_requirement = _normalize_requirement_text(str(only_row.get("requirement") or ""))
            only_detail = _normalize_requirement_text(str(only_row.get("detail_requirement") or ""))
            if (
                only_item_name
                and only_item_name == only_requirement == only_detail
                and _title_match(card_title, only_item_name)
            ):
                return []
        return normalized_rows

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    extracted_rows: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    section_context = section_context or {}
    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    leading_context = _leading_body_context_from_table_html(html_excerpt, card_title)
    preferred_item_context = str(
        leading_context
        or section_context.get("default_item_name")
        or section_context.get("section_title")
        or card_title
        or ""
    )
    inferred_item_name_context = ""
    inferred_requirement_context = ""

    for row in _extract_leading_body_rows_before_first_table():
        unit_key = (
            _normalize_requirement_text(str(row.get("result_note") or "")),
            _normalize_requirement_text(str(row.get("item_name") or "")),
            _normalize_requirement_text(str(row.get("requirement") or "")),
            _normalize_requirement_text(str(row.get("detail_requirement") or "")),
        )
        if unit_key in seen:
            continue
        seen.add(unit_key)
        extracted_rows.append(row)

    for idx, table in enumerate(tables, start=1):
        matrix = _table_visual_matrix(table, preserve_breaks=True)
        if not matrix:
            continue
        top_table_context, top_context_skip_rows = _top_table_context_from_matrix(
            matrix, card_title
        )
        preferred_item_context = str(
            leading_context
            or top_table_context
            or section_context.get("default_item_name")
            or section_context.get("section_title")
            or card_title
            or ""
        )
        max_cols_before_drop = max(len(row) for row in matrix)
        dropped_numbering_col = None
        numbering_source_matrix: list[list[str]] | None = None
        # Some broken HTML tables are really 3-column tables but arrive as 4-column
        # matrices because repeated header rows inherit a dangling rowspan value in col 0.
        # Normalize those back to a clean 3-column matrix before rule matching.
        if max_cols_before_drop == 4 and matrix:
            header3 = [_normalize_requirement_text(str(cell or "")) for cell in matrix[0][:3]]
            if header3 and all(header3):
                normalized_matrix: list[list[str]] = []
                converted = False
                for row in matrix:
                    padded = list(row) + [""] * max(0, 4 - len(row))
                    norm = [_normalize_requirement_text(str(cell or "")) for cell in padded[:4]]
                    if norm[1:] == header3:
                        normalized_matrix.append(list(header3))
                        converted = True
                        continue
                    if not norm[3]:
                        normalized_matrix.append(padded[:3])
                        converted = True
                        continue
                    normalized_matrix.append(padded[:4])
                if converted and all(len(row) == 3 for row in normalized_matrix):
                    matrix = normalized_matrix
                    max_cols_before_drop = 3
        if max_cols_before_drop in {4, 5}:
            numbering_source_matrix = [list(row) for row in matrix]
            matrix, dropped_numbering_col = _drop_numbering_column_from_matrix(matrix)
        max_cols = max(len(row) for row in matrix)
        if max_cols == 1:
            single_col_rows = _extract_single_column_table_rows(
                card,
                matrix,
                card_title,
                leading_context or preferred_item_context,
                has_header_row=bool(table.find("tr") and table.find("tr").find("th")),
            )
            for row in single_col_rows:
                unit_key = (
                    _normalize_requirement_text(str(row.get("result_note") or "")),
                    _normalize_requirement_text(str(row.get("item_name") or "")),
                    _normalize_requirement_text(str(row.get("requirement") or "")),
                    _normalize_requirement_text(str(row.get("detail_requirement") or "")),
                )
                if unit_key in seen:
                    continue
                seen.add(unit_key)
                extracted_rows.append(row)
            continue
        if max_cols not in {2, 3, 4}:
            continue

        if max_cols == 3 and _is_box_style_three_column_table(matrix):
            leading_text = _normalize_requirement_text(
                leading_context
                or preferred_item_context
                or card_title
                or card.requirement
                or getattr(card, "subject", None)
                or ""
            )
            if not leading_text:
                leading_text = _normalize_requirement_text(
                    str(getattr(card, "subject", None) or card.requirement or "")
                )
            for row in matrix:
                padded = list(row) + [""] * max(0, 3 - len(row))
                detail_requirement = _normalize_requirement_text(str(padded[2] or ""))
                if not detail_requirement:
                    continue
                detail_units = _split_table_cell_lines(detail_requirement)
                if len(detail_units) <= 1:
                    detail_units = _split_atomic_detail_units(detail_requirement) or [
                        detail_requirement
                    ]
                for detail_unit in detail_units:
                    cleaned_detail = _strip_trailing_orphan_bullet(
                        _normalize_requirement_text(detail_unit)
                    )
                    if not cleaned_detail or _is_only_page_info(cleaned_detail):
                        continue
                    unit_key = ("", leading_text, leading_text, cleaned_detail)
                    if unit_key in seen:
                        continue
                    seen.add(unit_key)
                    extracted_rows.append(
                        {
                            "item_name": leading_text,
                            "requirement": leading_text,
                            "detail_requirement": cleaned_detail,
                            "result_note": "",
                            "id_title_hint": "",
                            "build_method": "룰 기반(3단 표-박스형 본문)",
                            "table_rule_branch": "3단표-박스형본문",
                            "table_index": idx,
                        }
                    )
            continue

        first_tr = table.find("tr")
        has_header_row = bool(first_tr and first_tr.find("th"))
        is_single_row_header_continuation = _is_single_row_header_continuation_table(
            matrix, has_header_row
        )
        if is_single_row_header_continuation:
            has_header_row = False
        header_row = [str(cell or "").strip() for cell in matrix[0]]
        numeric_aux_third = max_cols == 3 and _is_numeric_auxiliary_third_column(matrix)
        is_two_level_three_col = max_cols == 3 and (
            _is_auxiliary_third_column_header(header_row) or numeric_aux_third
        )
        is_three_level_four_col_with_aux_last = (
            max_cols == 4 and _is_auxiliary_fourth_column_header(header_row)
        )
        is_grouped_three_col = max_cols == 3 and _is_grouped_three_level_header(header_row)
        embedded_first_data_row: list[str] | None = None
        is_box_like_single_row_three_col = False
        if has_header_row and max_cols == 3:
            split_cells: list[tuple[str, str]] = []
            for cell in header_row:
                parts = [
                    part.strip()
                    for part in re.split(r"\n\s*\n|\n", str(cell or "").strip())
                    if part.strip()
                ]
                head = parts[0] if parts else ""
                tail = "\n\n".join(parts[1:]).strip() if len(parts) > 1 else ""
                split_cells.append((head, tail))
            normalized_heads = [_normalize_requirement_text(head) for head, _ in split_cells]
            if (
                _is_auxiliary_third_column_header(normalized_heads)
                and sum(1 for _, tail in split_cells if _normalize_requirement_text(tail)) >= 2
            ):
                header_row = [head for head, _ in split_cells]
                matrix[0] = header_row
                embedded_first_data_row = [tail for _, tail in split_cells]

        data_rows = matrix[1:] if has_header_row and len(matrix) > 1 else matrix
        numbering_data_rows: list[list[str]] = []
        if numbering_source_matrix is not None:
            numbering_data_rows = (
                numbering_source_matrix[1:]
                if has_header_row and len(numbering_source_matrix) > 1
                else numbering_source_matrix
            )
        if embedded_first_data_row is not None:
            data_rows = [embedded_first_data_row, *data_rows]
        if max_cols == 2 and top_table_context and top_context_skip_rows:
            skip_count = max(top_context_skip_rows - (1 if has_header_row else 0), 0)
            if skip_count:
                data_rows = data_rows[skip_count:]

        table_rule_branch = (
            "3단표-박스형본문"
            if max_cols == 3 and _is_box_style_three_column_table(matrix)
            else (
                "3단표->2단표+비고/추가정보"
                if is_two_level_three_col
                else (
                    "4단표->3단표+추가정보"
                    if is_three_level_four_col_with_aux_last
                    else (
                        "3단표-그룹헤더"
                        if is_grouped_three_col
                        else (
                            "4단표-번호컬럼제거후3단처리"
                            if dropped_numbering_col is not None and max_cols == 3
                            else (
                                "3단표-단일행fallback"
                                if max_cols == 3 and len(matrix) == 1 and not has_header_row
                                else (
                                    "일반3단표"
                                    if max_cols == 3
                                    else ("2단표" if max_cols == 2 else f"{max_cols}단표")
                                )
                            )
                        )
                    )
                )
            )
        )

        for row_idx, row in enumerate(data_rows):
            padded = list(row) + [""] * max(0, max_cols - len(row))
            original_row_signature = [str(cell or "").strip() for cell in padded[:max_cols]]
            if has_header_row and original_row_signature == header_row:
                continue
            numbered_row = any(
                _has_leading_numbering(str(cell or "")) for cell in padded[:max_cols]
            )
            result_note = ""
            dropped_numbering_value = ""
            empty_leading_two_cols_three_col = (
                max_cols == 3
                and not has_header_row
                and not str(padded[0] or "").strip()
                and not str(padded[1] or "").strip()
                and str(padded[2] or "").strip()
            )
            if (
                dropped_numbering_col is not None
                and numbering_data_rows
                and row_idx < len(numbering_data_rows)
                and dropped_numbering_col < len(numbering_data_rows[row_idx])
            ):
                dropped_numbering_value = _normalize_requirement_text(
                    str(numbering_data_rows[row_idx][dropped_numbering_col] or "")
                )
            if max_cols == 2:
                item_name = _normalize_requirement_text(
                    str(two_col_item_name_override or "")
                ) or _normalize_two_col_table_item_name(
                    preferred_item_context,
                    card_title,
                )
                requirement = _normalize_requirement_text(str(padded[0] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[1] or ""))
            elif is_two_level_three_col:
                item_name = _normalize_requirement_text(
                    str(two_col_item_name_override or "")
                ) or _normalize_two_col_table_item_name(
                    preferred_item_context,
                    card_title,
                )
                requirement = _normalize_requirement_text(str(padded[0] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[1] or ""))
                result_note = _normalize_requirement_text(str(padded[2] or ""))
            elif max_cols == 3:
                if empty_leading_two_cols_three_col:
                    is_box_like_single_row_three_col = True
                    item_name = _normalize_requirement_text(
                        leading_context
                        or preferred_item_context
                        or card_title
                        or card.requirement
                        or getattr(card, "subject", None)
                        or ""
                    )
                    requirement = item_name
                    detail_requirement = _normalize_requirement_text(str(padded[2] or ""))
                elif len(matrix) == 1 and not has_header_row and leading_context:
                    item_name = _normalize_requirement_text(leading_context)
                else:
                    item_name = _normalize_three_col_table_item_name(
                        str(padded[0] or ""),
                        preferred_item_context,
                        card_title,
                    )
                if not is_box_like_single_row_three_col:
                    requirement = _normalize_requirement_text(str(padded[1] or ""))
                    detail_requirement = _normalize_requirement_text(str(padded[2] or ""))
            elif is_three_level_four_col_with_aux_last:
                if len(matrix) == 1 and not has_header_row and leading_context:
                    item_name = _normalize_requirement_text(leading_context)
                else:
                    item_name = _normalize_three_col_table_item_name(
                        str(padded[0] or ""),
                        preferred_item_context,
                        card_title,
                    )
                requirement = _normalize_requirement_text(str(padded[1] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[2] or ""))
                result_note = _normalize_requirement_text(str(padded[3] or ""))
            else:
                result_note = _normalize_requirement_text(str(padded[0] or ""))
                item_name = _normalize_requirement_text(str(padded[1] or ""))
                requirement = _normalize_requirement_text(str(padded[2] or ""))
                detail_requirement = _normalize_requirement_text(str(padded[3] or ""))

            if dropped_numbering_value:
                result_note = dropped_numbering_value

            if (
                _is_header_like_field(item_name)
                and _is_header_like_field(requirement)
                and detail_requirement
                and not _is_header_like_field(detail_requirement)
                and len(detail_requirement) <= 80
            ):
                inferred_item_name_context = detail_requirement
            if requirement and len(requirement) <= 40:
                inferred_requirement_context = requirement

            if not item_name and not requirement and not detail_requirement:
                continue
            row_signature = (
                [requirement, detail_requirement, result_note]
                if is_two_level_three_col
                else (
                    [item_name, requirement, detail_requirement]
                    if max_cols == 3
                    else (
                        [result_note, item_name, requirement, detail_requirement]
                        if max_cols == 4
                        else [requirement, detail_requirement]
                    )
                )
            )
            if has_header_row and row_signature == header_row:
                continue
            if not detail_requirement:
                continue
            if not requirement:
                if is_single_row_header_continuation and inferred_requirement_context:
                    requirement = inferred_requirement_context
                else:
                    requirement = str(
                        getattr(card, "subject", None) or card.requirement or ""
                    ).strip()
            if not item_name and is_two_level_three_col:
                item_name = _normalize_two_col_table_item_name(
                    preferred_item_context,
                    card_title,
                )
            if not item_name and max_cols == 3:
                item_name = str(
                    getattr(card, "subject", None)
                    or card.requirement
                    or getattr(card, "sub_subject", None)
                    or ""
                ).strip()
            if not item_name and max_cols == 4:
                item_name = str(
                    leading_context
                    or getattr(card, "subject", None)
                    or card.requirement
                    or getattr(card, "sub_subject", None)
                    or ""
                ).strip()
            if inferred_item_name_context and _is_noisy_table_item_name(item_name):
                item_name = inferred_item_name_context

            if is_box_like_single_row_three_col:
                if not item_name:
                    item_name = _normalize_requirement_text(
                        leading_context
                        or preferred_item_context
                        or card_title
                        or card.requirement
                        or getattr(card, "subject", None)
                        or ""
                    )
                if not requirement:
                    requirement = item_name or _normalize_requirement_text(
                        card.requirement or card_title or ""
                    )
                if not detail_requirement:
                    detail_requirement = _normalize_requirement_text(str(padded[2] or ""))

            detail_units = _split_table_cell_lines(detail_requirement)
            if len(detail_units) <= 1:
                if max_cols == 3 or is_two_level_three_col:
                    detail_units = _split_three_col_detail_units(detail_requirement)
                else:
                    detail_units = _split_atomic_detail_units(detail_requirement)
            for detail_unit in detail_units or [detail_requirement]:
                unit_key = (result_note, item_name, requirement, detail_unit)
                if unit_key in seen:
                    continue
                seen.add(unit_key)
                id_title_hint = ""
                if (
                    max_cols == 3
                    and leading_context
                    and leading_context != card_title
                    and _is_promotable_leading_table_heading(leading_context, card_title)
                ):
                    id_title_hint = leading_context
                extracted_rows.append(
                    {
                        "item_name": item_name,
                        "requirement": requirement,
                        "detail_requirement": detail_unit,
                        "result_note": result_note,
                        "id_title_hint": id_title_hint,
                        "table_index": idx,
                        "table_rule_branch": table_rule_branch,
                        "build_method": (
                            "룰 기반(3단 표-박스형 본문)"
                            if is_box_like_single_row_three_col
                            else (
                                "룰 기반(4단 표->3단 표+추가정보)"
                                if is_three_level_four_col_with_aux_last
                                else (
                                    "룰 기반(4단 표)"
                                    if max_cols == 4
                                    else (
                                        "룰 기반(3단 표->2단 표+추가정보)"
                                        if is_two_level_three_col
                                        else (
                                            "룰 기반(3단 표-그룹헤더)"
                                            if is_grouped_three_col
                                            else (
                                                "룰 기반(4단 표-번호컬럼 제거 후 3단 처리)"
                                                if dropped_numbering_col is not None
                                                and max_cols == 3
                                                else (
                                                    "룰 기반(넘버링 표->3단 처리)"
                                                    if max_cols == 2 and numbered_row
                                                    else (
                                                        "룰 기반(2단 표)"
                                                        if max_cols == 2
                                                        else "룰 기반(3단 표)"
                                                    )
                                                )
                                            )
                                        )
                                    )
                                )
                            )
                        ),
                    }
                )

        if max_cols == 3 and len(matrix) == 1 and not extracted_rows:
            padded = list(matrix[0]) + [""] * max(0, 3 - len(matrix[0]))
            fallback_item_name = _normalize_requirement_text(
                str(
                    leading_context
                    or preferred_item_context
                    or card_title
                    or getattr(card, "subject", None)
                    or card.requirement
                    or ""
                )
            )
            fallback_requirement = _normalize_requirement_text(
                str(padded[0] or "")
            ) or _normalize_requirement_text(
                str(card_title or getattr(card, "subject", None) or card.requirement or "")
            )
            fallback_detail = _normalize_requirement_text(str(padded[2] or ""))
            if fallback_item_name and fallback_requirement and fallback_detail:
                fallback_rows = _split_three_col_detail_units(fallback_detail) or [fallback_detail]
                for fallback_detail_unit in fallback_rows:
                    fallback_detail_unit = _normalize_requirement_text(fallback_detail_unit)
                    if not fallback_detail_unit:
                        continue
                    unit_key = ("", fallback_item_name, fallback_requirement, fallback_detail_unit)
                    if unit_key in seen:
                        continue
                    seen.add(unit_key)
                    extracted_rows.append(
                        {
                            "item_name": fallback_item_name,
                            "requirement": fallback_requirement,
                            "detail_requirement": fallback_detail_unit,
                            "result_note": "",
                            "id_title_hint": "",
                            "build_method": "룰 기반(3단 표-단일행 fallback)",
                            "table_rule_branch": "3단표-단일행fallback",
                            "table_index": idx,
                        }
                    )

    return extracted_rows


def _fallback_card_requirement_rows(card: RfpCard, section_context: dict) -> list[dict]:
    lines = [
        line.strip(" -\u2022\t")
        for line in _html_excerpt_lines(str(card.html_excerpt or ""))
        if line.strip()
    ]
    title = str(getattr(card, "subject", None) or card.requirement or "").strip() or str(
        card.requirement
    )
    item_name = str(
        getattr(card, "sub_subject", None)
        or section_context.get("default_item_name")
        or section_context.get("section_title")
        or title
    ).strip()
    content_lines = [line for line in lines if line != title]
    if not content_lines:
        content_lines = [title]
    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for line in content_lines[:20]:
        requirement = title
        detail_requirement = re.sub(r"\s+", " ", line).strip()
        for detail_unit in _split_atomic_detail_units(detail_requirement) or [detail_requirement]:
            key = (item_name, requirement, detail_unit)
            if not detail_unit or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "item_name": item_name,
                    "requirement": requirement,
                    "detail_requirement": detail_unit,
                    "result_note": "",
                    "build_method": "룰 기반(fallback)",
                }
            )
    return rows or [
        {
            "item_name": item_name,
            "requirement": title,
            "detail_requirement": title,
            "result_note": "",
            "build_method": "룰 기반(fallback)",
        }
    ]


def _fallback_table_card_rows(card: RfpCard, section_context: dict) -> list[dict]:
    # 표 파싱에 실패한 표 카드에 대해, LLM을 호출하지 않고 룰 기반으로
    # 강제 분할 추출하는 안전망 함수
    html_excerpt = str(card.html_excerpt or "").strip()
    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]

    rows: list[dict] = []
    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    item_name = str(
        getattr(card, "sub_subject", None)
        or section_context.get("default_item_name")
        or section_context.get("section_title")
        or card_title
    ).strip()

    seen: set[tuple[str, str, str]] = set()

    for table in tables:
        records = _table_row_records_linewise(table)
        if not records:
            continue
        first_tr = table.find("tr")
        has_header_row = bool(first_tr and first_tr.find("th"))

        data_rows = records[1:] if has_header_row and len(records) > 1 else records

        for _, row in data_rows:
            # 빈 셀 제외한 유효 텍스트들
            cells = [str(c).strip() for c in row if str(c).strip()]
            if not cells:
                continue

            # 셀 개수에 따른 휴리스틱 매핑
            if len(cells) == 1:
                requirement = card_title
                detail_requirement = cells[0]
            elif len(cells) == 2:
                requirement = cells[0]
                detail_requirement = cells[1]
            else:
                # 3개 이상인 경우: 마지막 셀을 detail_requirement로 하고
                # 앞의 셀들은 requirement로 결합
                requirement = " > ".join(cells[:-1])
                detail_requirement = cells[-1]

            requirement = _normalize_requirement_text(requirement)
            detail_requirement = _normalize_requirement_text(detail_requirement)
            # fallback 경로는 HTML 줄 단위 결과를 더 쪼개지 않고 그대로 유지한다.
            # 여기서 추가 분해를 하면 <p>/<li> 기준으로 살아난 줄이 다시 합쳐지거나,
            # 긴 표 셀이 과도하게 여러 조각으로 갈라져 속도와 안정성이 떨어진다.
            fallback_detail_units = [
                _normalize_requirement_text(part)
                for part in detail_requirement.split("\n")
                if _normalize_requirement_text(part)
            ] or [detail_requirement]

            for detail_unit in fallback_detail_units:
                key = (item_name, requirement, detail_unit)
                if not detail_unit or key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "item_name": item_name,
                        "requirement": requirement,
                        "detail_requirement": detail_unit,
                        "result_note": "",
                        "build_method": "룰 기반(표 fallback)",
                    }
                )

    if not rows:
        return _fallback_card_requirement_rows(card, section_context)
    return rows
