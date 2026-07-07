"""섹션별 요구사항표 조립 — step5 오케스트레이터. rfpmatch/step456_shared.py 이식.

카드 → (rowbuild.py 규칙엔진 우선, 막히면 LLM 폴백) → 요구사항 ID 부여까지의 전체 흐름.
원본은 Streamlit(progress bar, session_state, 카드마다 디스크에 디버그 스냅샷 저장, 단계별
elapsed-time 트레이스)에 강하게 결합돼 있었다. 여기서는:
- LLM 호출은 raw OpenAI SDK 대신 app.llm.factory + structured_output으로 교체
- 진행 상황은 선택적 on_progress(text) 콜백으로만 노출(기본 no-op)
- 카드별 디스크 스냅샷/세부 트레이스는 제거 — debug_rows는 메모리에 쌓아 최종 반환값으로만 전달
- 토큰/비용 집계는 app/llm/usage.py가 이미 전담하므로 이 엔진에서는 추적하지 않음(M2 결정과 동일)
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable

from bs4 import BeautifulSoup
from pydantic import BaseModel

from .models import RfpCard
from .partition import (
    _inherits_requirement_id_from_previous_table,
    _is_table_followup_common_note_card,
    _partition_card_for_requirement_build,
    _row_block_name,
    _table_has_nested_table,
    _table_visual_matrix,
)
from .rowbuild import (
    _build_source_detail_label,
    _describe_build_method,
    _drop_numbering_column_from_matrix,
    _expand_requirement_rows,
    _extract_atomic_body_rows,
    _extract_rows_from_table_card,
    _fallback_card_requirement_rows,
    _fallback_item_name_for_marker_only_row,
    _fallback_table_card_rows,
    _flatten_body_requirement_for_save,
    _group_cards_by_section,
    _infer_dash_mid_item_name_from_text,
    _infer_korean_heading_before_dash_detail,
    _infer_section_body_hierarchy_profile,
    _is_auxiliary_third_column_header,
    _is_header_like_requirement_row,
    _is_redundant_same_text_requirement_row,
    _leading_body_context_from_table_html,
    _normalize_item_name_for_row,
    _normalize_table_item_name_with_card_title,
    _normalize_two_col_table_item_name,
    _row_requirement_id_prefix,
    _section_requirement_prefix,
    _select_requirement_id_source,
    _should_override_requirement_with_korean_heading,
    _should_skip_llm_for_heading_only_body_card,
    _should_skip_requirement_extraction,
    _strip_trailing_orphan_bullet,
    _table_rule_branch_label,
    _top_table_context_from_matrix,
    _unify_similar_table_item_names,
)
from .text_utils import (
    _html_excerpt_lines,
    _is_title_like_requirement_text,
    _normalize_requirement_text,
    _plain_text_from_html_excerpt,
)

logger = logging.getLogger(__name__)


def _is_trivial_single_requirement_section(rows: list[dict]) -> bool:
    if len(rows) != 1:
        return False
    row = rows[0] or {}
    item_name = _normalize_requirement_text(str(row.get("항목명") or row.get("item_name") or ""))
    requirement = _normalize_requirement_text(
        str(row.get("요구사항") or row.get("requirement") or "")
    )
    detail_requirement = _normalize_requirement_text(
        str(row.get("상세요건") or row.get("detail_requirement") or "")
    )
    if not item_name or not requirement or not detail_requirement:
        return False
    if item_name == requirement == detail_requirement:
        return True
    part_value = _normalize_requirement_text(
        str(row.get("Part") or row.get("part") or row.get("group") or "")
    )
    section_value = _normalize_requirement_text(str(row.get("Section") or row.get("section") or ""))
    category_value = _normalize_requirement_text(
        str(
            row.get("Category")
            or row.get("카테고리")
            or row.get("category")
            or row.get("requirement")
            or ""
        )
    )
    meta_label_hits = sum(
        1
        for token in ("Part", "Section", "Category", "카테고리")
        if token.lower() in detail_requirement.lower()
    )
    if meta_label_hits >= 2:
        return True
    return bool(
        part_value
        and section_value
        and category_value
        and part_value in detail_requirement
        and section_value in detail_requirement
        and category_value in detail_requirement
    )


def _normalize_section_requirement_tables(
    section_tables: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    normalized_tables: dict[str, list[dict]] = {}
    for section_name, rows in section_tables.items():
        normalized_rows: list[dict] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            part_value = str(row.get("Part") or row.get("part") or row.get("group") or "").strip()
            section_value = str(row.get("Section") or row.get("section") or "").strip()
            cleaned_row = {
                "블럭명": row.get(
                    "블럭명", row.get("block_name", row.get("card_no", row.get("_card_no", "")))
                ),
                "요구사항 ID": row.get("요구사항 ID", ""),
                "항목명": row.get("항목명", ""),
                "요구사항": row.get("요구사항", ""),
                "상세요건": row.get("상세요건", ""),
                "추가정보": row.get("추가정보", ""),
                "페이지": row.get("페이지", ""),
                "Part": part_value,
                "Section": section_value,
                "Category": row.get("Category", row.get("카테고리", "")),
                "생성 출처": row.get("생성 출처", ""),
                "생성 방식": row.get("생성 방식", ""),
            }
            for key, value in row.items():
                if key in cleaned_row or key in {"적용룰", "applied_rule"}:
                    continue
                cleaned_row[key] = value
            normalized_rows.append(cleaned_row)
        normalized_tables[section_name] = normalized_rows
    return normalized_tables


class _TwoColItemName(BaseModel):
    item_name: str = ""


class _CardRequirementRow(BaseModel):
    item_name: str = ""
    requirement: str = ""
    detail_requirement: str = ""
    result_note: str = ""
    id_title_hint: str = ""


class _CardRequirementRows(BaseModel):
    rows: list[_CardRequirementRow] = []


def _infer_two_col_table_item_name_via_llm(client, card: RfpCard, section_context: dict) -> str:
    """2단 표 카드의 level-1 item_name을 LLM으로 추론. client는 app.llm.base.AsyncLlmClient."""
    from app.llm.base import Message
    from prototype.v2.async_run import run_coro

    html_excerpt = str(card.html_excerpt or "").strip()
    if "<table" not in html_excerpt.lower():
        return ""

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    if not tables:
        return ""

    target_table = None
    target_matrix: list[list[str]] = []
    for table in tables:
        matrix = _table_visual_matrix(table, preserve_breaks=True)
        if not matrix:
            continue
        max_cols_before_drop = max(len(row) for row in matrix)
        if max_cols_before_drop != 4:
            matrix, _ = _drop_numbering_column_from_matrix(matrix)
        max_cols = max(len(row) for row in matrix)
        header_row = [str(cell or "").strip() for cell in matrix[0]] if matrix else []
        if max_cols == 2 or (max_cols == 3 and _is_auxiliary_third_column_header(header_row)):
            target_table = table
            target_matrix = matrix
            break

    if target_table is None or not target_matrix:
        return ""

    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    leading_context = _leading_body_context_from_table_html(html_excerpt, card_title)
    top_table_context, _ = _top_table_context_from_matrix(target_matrix, card_title)
    rule_based_hint = _normalize_two_col_table_item_name(
        str(
            leading_context
            or top_table_context
            or card.requirement
            or getattr(card, "subject", None)
            or ""
        ),
        card_title,
    )
    first_column_preview: list[str] = []
    row_start_idx = 1 if len(target_matrix) > 1 else 0
    for row in target_matrix[row_start_idx : row_start_idx + 6]:
        first_value = _normalize_requirement_text(str((row[0] if row else "") or ""))
        if first_value and first_value not in first_column_preview:
            first_column_preview.append(first_value)
    payload = {
        "section_context": {
            "section_title": str(section_context.get("section_title") or ""),
            "default_item_name": str(section_context.get("default_item_name") or ""),
        },
        "card": {
            "card_no": getattr(card, "card_no", None) or card.card_id,
            "subject": card_title,
            "requirement": str(card.requirement or ""),
            "leading_context": leading_context,
            "top_table_context": top_table_context,
            "rule_based_hint": rule_based_hint,
            "table_header": target_matrix[0] if target_matrix else [],
            "first_column_preview": first_column_preview,
            "table_rows_preview": target_matrix[:6],
            "html_excerpt": str(target_table)[:8000],
        },
    }
    system_prompt = (
        "You infer only the level-1 item_name for one 2-column RFP table. "
        "Return JSON object only with key: item_name. "
        "Choose the most specific accurate category noun phrase that sits exactly one level "
        "above the table's first-column groups. "
        "Do not return a full sentence. Do not return an explanation. "
        "Prefer a compact domain/category such as '정보처리시스템', '개인정보처리시스템', '공통', "
        "'서비스 설계(UX)', '그래픽 디자인(UI)'. "
        "Choose the best item_name by jointly considering the sentence before the table, the "
        "table header, and the section/card title. "
        "If the sentence before the table gives a narrower parent category than the section "
        "title, prefer that narrower category. "
        "Use the card title, leading_context, top_table_context, first_column_preview, and "
        "html_excerpt together. "
        "Do not just repeat section_context.default_item_name unless there is no narrower "
        "table-specific category in the card title or surrounding context. "
        "Different tables in the same section should produce different item_name values when "
        "their surrounding context points to different domains. "
        "The first-column values are row groups, not the level-1 item_name. Infer the parent "
        "category above them. "
        "Prefer a phrase explicitly supported by card title or nearby context over a generic "
        "shared section label. "
        "If rule_based_hint is already a more specific domain/category than default_item_name, "
        "prefer that level of specificity."
    )

    async def _call() -> _TwoColItemName:
        return await client.structured_output(
            [
                Message(role="system", content=system_prompt),
                Message(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            _TwoColItemName,
            purpose="rfpmatch_item_name",
            max_tokens=500,
        )

    try:
        result = run_coro(_call())
        item_name = _normalize_requirement_text(result.item_name)
    except Exception:
        item_name = ""

    default_item_name = _normalize_requirement_text(
        str(section_context.get("default_item_name") or "")
    )
    matches_generic_default = (
        item_name
        and default_item_name
        and item_name == default_item_name
        and rule_based_hint
        and rule_based_hint != default_item_name
    )
    if matches_generic_default or (not item_name and rule_based_hint):
        item_name = rule_based_hint
    return item_name


def _should_route_table_card_to_llm(card: RfpCard) -> bool:
    html_excerpt = str(card.html_excerpt or "").strip()
    if "<table" not in html_excerpt.lower():
        return False

    soup = BeautifulSoup(html_excerpt, "html.parser")
    tables = [table for table in soup.find_all("table") if table.find_parent("table") is None]
    if not tables:
        return False

    if any(_table_has_nested_table(table) for table in tables):
        return True

    saw_supported_table = False
    saw_unsupported_table = False
    for table in tables:
        matrix = _table_visual_matrix(table, preserve_breaks=True)
        if not matrix:
            continue
        max_cols = max(len(row) for row in matrix)
        if max_cols in {2, 3, 4}:
            saw_supported_table = True
            continue
        saw_unsupported_table = True
        if max_cols > 4:
            return True

    # If a single card mixes rule-supported tables with unsupported wide tables,
    # partial rule extraction tends to drop the complex table content. In that
    # case, let LLM see the whole card structure instead of accepting a partial hit.
    return saw_supported_table and saw_unsupported_table


def _section_context_fallback(section_name: str, cards: list[RfpCard]) -> dict:
    first_card = cards[0] if cards else None
    body_hierarchy_profile = _infer_section_body_hierarchy_profile(section_name, cards)
    hierarchy_sequence = body_hierarchy_profile.get("family_sequence") or []
    sequence_text = " > ".join(
        {
            "heading": "숫자/장절",
            "hangul_syllable": "가.나.다.",
            "dash_mid": "-(중간)",
            "hangul_jamo": "ㄱ.ㄴ.ㄷ.",
            "digit_dot": "1.",
            "digit_paren": "1)",
            "latin": "a.",
            "symbol": "•/○/O",
            "dash_low": "-(하위)",
            "other": "기타",
        }.get(family, family)
        for family in hierarchy_sequence
    )
    return {
        "section_title": section_name,
        "id_prefix": _section_requirement_prefix(section_name),
        "default_item_name": str(
            getattr(first_card, "subject", None)
            or getattr(first_card, "requirement", None)
            or getattr(first_card, "part", "")
            or section_name
        ).strip()
        or section_name,
        "section_summary": "",
        # 여기서 말하는 블록은 섹션 안에서 카드로 먼저 분리된 뒤,
        # 그 카드 내부에서 본문/표로 다시 나뉘어 생성되는 개별 조각을 뜻한다.
        "hierarchy_guidance": sequence_text
        or "섹션 블록(카드 내부 본문/표 조각) 단위로 계층을 추론",
        "body_hierarchy_profile": body_hierarchy_profile,
        "body_block_rulesets": [
            {
                # 블록 = 카드 내부의 본문/표 조각 단위
                "block_kind": (
                    "shared_parent"
                    if body_hierarchy_profile.get("uses_shared_parent_root")
                    else "hierarchical"
                ),
                "family_sequence": body_hierarchy_profile.get("family_sequence") or [],
                "root_family": body_hierarchy_profile.get("root_family") or "",
                "requirement_family": body_hierarchy_profile.get("requirement_family") or "",
                "detail_family": body_hierarchy_profile.get("detail_family") or "",
            }
        ],
    }


def _build_card_requirement_rows_via_llm(
    client,
    card: RfpCard,
    section_context: dict,
    *,
    use_llm: bool = True,
) -> tuple[list[dict], str]:
    """카드 하나 → 요구사항 행 리스트. 규칙 기반 우선, 막히면 LLM 폴백.

    (rows, build_source_label) 반환.
    """
    from app.llm.base import Message
    from prototype.v2.async_run import run_coro

    section_context = dict(section_context or {})
    body_fragment_level = getattr(card, "body_fragment_level", None)
    if body_fragment_level is not None:
        section_context["body_fragment_level"] = body_fragment_level

    html_excerpt = str(card.html_excerpt or "")
    has_table_card = "<table" in html_excerpt.lower()
    has_nested_table_card = False
    if has_table_card:
        soup_for_nested = BeautifulSoup(html_excerpt, "html.parser")
        has_nested_table_card = any(
            _table_has_nested_table(table)
            for table in soup_for_nested.find_all("table")
            if table.find_parent("table") is None
        )
    force_llm_for_table_card = has_table_card and _should_route_table_card_to_llm(card)

    rule_based_table_rows = (
        []
        if force_llm_for_table_card
        else _extract_rows_from_table_card(card, section_context=section_context)
    )
    if rule_based_table_rows:
        return rule_based_table_rows, "RULE_BASED_TABLE"
    if has_table_card and not force_llm_for_table_card:
        fallback_table_rows = _fallback_table_card_rows(card, section_context)
        if fallback_table_rows:
            return fallback_table_rows, "RULE_BASED_TABLE_FALLBACK"

    html_lines = _html_excerpt_lines(html_excerpt, include_tables=has_table_card)
    body_text = "\n".join(html_lines)
    card_title = str(getattr(card, "subject", None) or card.requirement or "").strip()
    if not force_llm_for_table_card:
        atomic_rows = _extract_atomic_body_rows(
            html_lines,
            title=card_title,
            default_item_name=str(
                card_title
                or getattr(card, "sub_subject", None)
                or section_context.get("default_item_name")
                or section_context.get("section_title")
                or ""
            ),
            build_method="룰 기반(단일 행 본문)",
        )
        if atomic_rows:
            return atomic_rows, "RULE_BASED_BODY"
    if not use_llm:
        fallback_rows = (
            _fallback_table_card_rows(card, section_context)
            if has_table_card
            else _fallback_card_requirement_rows(card, section_context)
        )
        return fallback_rows, "RULE_BASED_FALLBACK_NO_LLM"
    if not has_table_card and _should_skip_llm_for_heading_only_body_card(body_text, card_title):
        return [], "SKIPPED_HEADING_ONLY_BODY"

    payload = {
        "section_context": section_context,
        "card": {
            "card_no": getattr(card, "card_no", None) or card.card_id,
            "requirement": card.requirement,
            "subject": getattr(card, "subject", None),
            "sub_subject": getattr(card, "sub_subject", None),
            "part": getattr(card, "part", None) or "",
            "section": card.section,
            "has_table": has_table_card,
            "has_nested_table": has_nested_table_card,
            "html_excerpt": html_excerpt[:16000],
            "html_lines": body_text[:12000],
        },
    }
    system_prompt = (
        "You convert one RFP card into atomic requirement table rows. "
        "Return only a JSON object with a single key rows, whose value is an array. "
        "Each array item must have keys: item_name, requirement, "
        "detail_requirement, result_note. "
        "All output values must be plain text only, no HTML, no markdown. "
        "Use the card HTML excerpt structure and extracted HTML lines as the primary source "
        "for hierarchy, nesting, list grouping, heading emphasis, and structural boundaries. "
        "Treat each visible <p> or <li> line as an atomic source line unless the source "
        "explicitly keeps two lines in the same sentence. "
        "Do not merge separate <p>/<li> lines back into one row just because they are "
        "adjacent in the same list item. "
        "Ignore class/style/id and focus only on semantic structure and source text. "
        "requirement must be a heading-like label or short requirement title, not a full "
        "explanatory sentence. "
        "If explanatory prose is attached to a requirement heading, keep only the heading in "
        "requirement and move the prose into detail_requirement. "
        "If a candidate requirement is a sentence, that means the layer is wrong; demote that "
        "sentence into detail_requirement and keep requirement as the nearest heading-like "
        "label instead. "
        "detail_requirement must be atomic and specific, usually one sentence and at most two "
        "short sentences. "
        "Preserve source order. Do not invent content. "
        "Use the source text verbatim whenever possible; do not paraphrase, summarize, or "
        "rewrite. "
        "Do not drop bullet markers, list markers, or line breaks that carry meaning in the "
        "source text. "
        "If the source uses bullets such as -, *, •, ◦, □, keep those bullet markers in the "
        "output text. "
        "When splitting one source block into multiple rows, each row must preserve the "
        "original bullet/list marker and wording of that unit. "
        "If the source card contains a nested table, treat the nested table as a real "
        "lower-level structure and do not collapse it into the parent table row. "
        "If one detailed line contains chained inline markers like '가. 나. 다. 라. ...', do "
        "not drop any of those markers; keep the full original marker chain in the resulting "
        "detail_requirement text. "
        "For body content, first infer a clean hierarchy of 1-level, 2-level, 3-level, "
        "4-level, or 5-level from the source structure. "
        "Then map it strictly as follows: 1-level body => detail_requirement only; 2-level "
        "body => requirement + detail_requirement; 3-level body => item_name + requirement + "
        "detail_requirement; 4-level or 5-level body => item_name + requirement + merged "
        "detail_requirements. "
        "When the body has only 1 visible level, use the card title or section context for "
        "the upper fields and place the actual body line in detail_requirement. "
        "When the body has 2 visible levels, use the level-1 line as requirement and the "
        "level-2 line as detail_requirement. "
        "When the body has 3 visible levels, use level-1 as item_name, level-2 as "
        "requirement, and level-3 as detail_requirement. "
        "When the body has 4 or more visible levels (such as 5 levels), use level-1 as "
        "item_name, level-2 as requirement, and merge all subsequent levels (level-3, "
        "level-4, level-5, etc.) into detail_requirement (separated by newlines). "
        "If a 3-level body detail_requirement contains more than two sentences, split it "
        "into multiple rows so each detail_requirement contains one sentence or at most two "
        "short sentences. "
        "If the body has a shared parent heading such as '○ 공통' and child items labeled "
        "'가.', '나.', '다.', use the full shared parent heading line including its bullet "
        "marker as item_name, use the full child heading line such as '가. ...', '나. ...', "
        "'다. ...' as requirement, and split each '- ' bullet under that child into separate "
        "detail_requirement rows. "
        "If the card body starts with the card title and then has a child heading such as "
        "'가. ...' followed by bullet lines, use the card title as item_name, use the full "
        "child heading line as requirement, and split each bullet line below it into "
        "separate detail_requirement rows. "
        "For 3-level tables, map the hierarchy into item_name > requirement > "
        "detail_requirement. "
        "For 2-level tables, use the section subheading or section context as the first "
        "level and merge the table rows under it. "
        "For body requirements, infer a 3-level hierarchy from the text structure. "
        "If one card contains multiple distinct detailed requirements, return multiple rows. "
        "Never leave any of the three fields empty."
    )

    async def _call() -> _CardRequirementRows:
        return await client.structured_output(
            [
                Message(role="system", content=system_prompt),
                Message(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            _CardRequirementRows,
            purpose="rfpmatch_card_rows",
            max_tokens=8000,
        )

    try:
        result = run_coro(_call())
    except Exception as exc:
        return [], f"ERROR: {exc}"

    rows: list[dict] = []
    for item in result.rows:
        item_name = _normalize_requirement_text(item.item_name)
        requirement = _normalize_requirement_text(item.requirement)
        detail_requirement = _normalize_requirement_text(item.detail_requirement)
        if not detail_requirement:
            continue
        if not requirement:
            requirement = card_title or "요구사항"
        if not item_name:
            item_name = str(
                getattr(card, "sub_subject", None)
                or section_context.get("default_item_name")
                or section_context.get("section_title")
                or requirement
            ).strip()
        rows.append(
            {
                "item_name": item_name,
                "requirement": requirement,
                "detail_requirement": detail_requirement,
                "result_note": _normalize_requirement_text(item.result_note),
                "id_title_hint": _normalize_requirement_text(item.id_title_hint),
                "build_method": "LLM",
            }
        )
    return rows, "LLM"


def _merge_schedule_continuation_rows(rows: list[dict]) -> list[dict]:
    """M-day 오프셋처럼 직전 행의 연속으로 보이는 일정 상세를 하나로 합친다."""
    merged: list[dict] = []
    for row in rows:
        current_item = _normalize_requirement_text(str(row.get("item_name") or ""))
        current_req = _normalize_requirement_text(str(row.get("requirement") or ""))
        current_detail = _normalize_requirement_text(str(row.get("detail_requirement") or ""))
        current_note = _normalize_requirement_text(str(row.get("result_note") or ""))
        if merged:
            prev = merged[-1]
            prev_item = _normalize_requirement_text(str(prev.get("item_name") or ""))
            prev_req = _normalize_requirement_text(str(prev.get("requirement") or ""))
            prev_detail = _normalize_requirement_text(str(prev.get("detail_requirement") or ""))
            prev_note = _normalize_requirement_text(str(prev.get("result_note") or ""))
            if (
                prev_item
                and prev_item == current_item
                and prev_req == current_req
                and prev_note == current_note
                and re.match(r"^[\-*·ㆍ−–—]+\s*.+$", prev_detail)
                and re.match(
                    r"^M\s*-\s*\d+(?:\s*(?:개월|주|일|개월차|주차|월|년|단계|시점|전|후))?.+$",
                    current_detail,
                    flags=re.IGNORECASE,
                )
            ):
                merged[-1] = {
                    **prev,
                    "detail_requirement": f"{prev_detail}\n{current_detail}".strip(),
                }
                continue
        merged.append(row)
    return merged


def build_section_requirement_tables(
    cards: list[RfpCard],
    *,
    client=None,
    use_llm: bool = True,
    disable_dedup: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """카드 리스트 → 섹션별 요구사항표. (section_tables, debug_rows) 반환.

    client 미지정이고 use_llm=True면 app.llm.factory.build_llm_client(Settings())로 생성.
    on_progress(text)는 선택적 진행상황 콜백 — Streamlit progress bar를 대체.
    """
    if use_llm and client is None:
        from app.core.config import Settings
        from app.llm.factory import build_llm_client

        client = build_llm_client(Settings())

    def _progress(text: str) -> None:
        if on_progress is not None:
            on_progress(text)

    expanded_cards: list[RfpCard] = []
    for card in cards:
        expanded_cards.extend(_partition_card_for_requirement_build(card))
    grouped_cards = _group_cards_by_section(expanded_cards)
    section_tables: dict[str, list[dict]] = {}
    debug_rows: list[dict] = []
    two_level_table_methods = {
        "룰 기반(2단 표)",
        "룰 기반(3단 표->2단 표+추가정보)",
    }

    for section_name, section_cards in grouped_cards:
        _progress(f"섹션 컨텍스트 생성 중: {section_name}")
        section_context = _section_context_fallback(section_name, section_cards)
        prefix = _section_requirement_prefix(str(section_context.get("id_prefix") or section_name))
        section_rows: list[dict] = []
        previous_section_card: RfpCard | None = None

        for card in section_cards:
            card_label = f"{section_name} / {getattr(card, 'card_no', card.card_id)}"
            _progress(f"카드 분석 중: {card_label}")
            t0 = time.time()

            sub_subject = str(getattr(card, "sub_subject", "") or "").strip()
            build_source = (
                "표"
                if sub_subject.startswith("표") or "<table" in str(card.html_excerpt or "").lower()
                else "본문"
            )
            current_debug_row = {
                "section": section_name,
                "card_no": getattr(card, "card_no", None) or card.card_id,
                "subject": getattr(card, "subject", None) or card.requirement,
                "build_source": build_source,
                "body_fragment_level": getattr(card, "body_fragment_level", None),
                "raw_row_count": 0,
                "saved_row_count": 0,
                "filtered_out_reason": "",
                "elapsed_seconds": 0.0,
            }
            debug_rows.append(current_debug_row)

            is_table_followup_common_note = _is_table_followup_common_note_card(
                previous_section_card, card
            )
            inherit_prev_table_prefix = (
                _inherits_requirement_id_from_previous_table(previous_section_card, card)
                and not is_table_followup_common_note
            )

            skip_card, skip_reason = _should_skip_requirement_extraction(card)
            if skip_card:
                current_debug_row["filtered_out_reason"] = f"SKIPPED: {skip_reason}"
                previous_section_card = card
                continue

            try:
                card_rows, card_raw = _build_card_requirement_rows_via_llm(
                    client, card, section_context, use_llm=use_llm
                )
            except Exception as exc:
                card_rows, card_raw = [], f"ERROR: {exc}"
            current_debug_row["raw_row_count"] = len(card_rows)

            card_rows = _expand_requirement_rows(card_rows)

            item_name_llm_called = False
            if "<table" in str(card.html_excerpt or "").lower():
                eligible_table_rows = [
                    item
                    for item in card_rows
                    if isinstance(item, dict)
                    and _normalize_requirement_text(str(item.get("build_method") or ""))
                    in two_level_table_methods
                ]
                if eligible_table_rows and use_llm and client is not None:
                    _progress(f"카드 분석 중(항목명 보정): {card_label}")
                    try:
                        inferred_item_name = _normalize_requirement_text(
                            _infer_two_col_table_item_name_via_llm(client, card, section_context)
                        )
                        item_name_llm_called = bool(inferred_item_name)
                        if inferred_item_name:
                            for item in eligible_table_rows:
                                item["item_name"] = inferred_item_name
                                build_method = _normalize_requirement_text(
                                    str(item.get("build_method") or "")
                                )
                                if build_method and "항목명LLM" not in build_method:
                                    item["build_method"] = f"{build_method}+항목명LLM"
                    except Exception:
                        logger.debug("항목명 LLM 보정 실패: %s", card_label, exc_info=True)
                    _unify_similar_table_item_names(eligible_table_rows)

            card_rows = _merge_schedule_continuation_rows(card_rows)

            save_preview_rows: list[dict] = []
            for item in card_rows:
                block_name = _row_block_name(card, item, build_source)
                card_title_text = _normalize_requirement_text(
                    str(getattr(card, "subject", None) or card.requirement or "")
                )
                raw_item_name = _normalize_requirement_text(str(item.get("item_name") or ""))
                requirement = _strip_trailing_orphan_bullet(
                    _normalize_requirement_text(str(item.get("requirement") or "")),
                    source_tag=build_source,
                )
                detail_requirement = _strip_trailing_orphan_bullet(
                    _normalize_requirement_text(str(item.get("detail_requirement") or "")),
                    source_tag=build_source,
                )
                result_note = _strip_trailing_orphan_bullet(
                    _normalize_requirement_text(str(item.get("result_note") or "")),
                    source_tag=build_source,
                )
                special_rule_applied = bool(item.get("special_rule_applied"))
                build_method = _normalize_requirement_text(str(item.get("build_method") or "")) or (
                    "룰 기반" if str(card_raw).startswith("RULE_BASED") else "LLM"
                )
                table_rule_branch = (
                    _table_rule_branch_label(build_method) if build_source == "표" else ""
                )
                dash_mid_item_name = ""
                default_item_name_text = _normalize_requirement_text(
                    str(section_context.get("default_item_name") or "")
                )
                part_text = _normalize_requirement_text(str(getattr(card, "part", None) or ""))
                section_title_text = _normalize_requirement_text(
                    str(
                        section_context.get("section_title")
                        or section_name
                        or getattr(card, "section", "")
                        or ""
                    )
                )
                if special_rule_applied or build_source == "본문":
                    # 본문은 atomic 평탄화만 허용하고, 저장 직전 항목명/요구사항 승격은 하지 않는다.
                    item_name = raw_item_name
                else:
                    if build_method.startswith("룰 기반(4단 표"):
                        item_name = raw_item_name
                    else:
                        item_name = _strip_trailing_orphan_bullet(
                            _normalize_item_name_for_row(
                                raw_item_name, str(item.get("requirement") or "")
                            )
                        )
                    card_plain_text = _plain_text_from_html_excerpt(str(card.html_excerpt or ""))
                    category_text = card_title_text
                    item_name = _fallback_item_name_for_marker_only_row(
                        item_name, category_text, section_title_text, default_item_name_text
                    )
                    item_name = _normalize_table_item_name_with_card_title(
                        item_name,
                        card_title=card_title_text,
                        default_item_name=default_item_name_text,
                        card_requirement=str(card.requirement or ""),
                        section_title=section_title_text,
                        part_text=part_text,
                    )
                    korean_heading_before_dash = _infer_korean_heading_before_dash_detail(
                        card_plain_text or str(card.subject or card.requirement or "")
                    )
                    if (
                        _should_override_requirement_with_korean_heading(build_source)
                        and korean_heading_before_dash
                        and item_name == requirement
                        and _is_title_like_requirement_text(item_name)
                        and re.match(r"^[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+\s*", detail_requirement)
                    ):
                        requirement = korean_heading_before_dash
                    detail_is_dash_like = bool(
                        re.match(r"^[\-*•▪■◆▶◦○□◇·ㆍ−–—⦁]+\s*", detail_requirement)
                    )
                    if (
                        item_name.startswith("-")
                        and detail_is_dash_like
                        and re.match(r"^ㄱ[\.\)]\s*", requirement) is None
                        and (category_text or section_title_text)
                    ):
                        fallback_title = (
                            category_text or default_item_name_text or section_title_text
                        )
                        item_name = fallback_title
                        requirement = fallback_title
                    has_followup_korean_jamo = bool(
                        re.search(r"^ㄱ[\.\)]\s*", card_plain_text, flags=re.MULTILINE)
                    )
                    if item_name.startswith("-") and not has_followup_korean_jamo:
                        context_item_name = (
                            category_text or default_item_name_text or section_title_text
                        )
                        if context_item_name and not context_item_name.startswith("-"):
                            item_name = context_item_name
                    dash_mid_item_name = _infer_dash_mid_item_name_from_text(
                        card_plain_text or str(card.subject or card.requirement or "")
                    )
                if (
                    build_source != "본문"
                    and dash_mid_item_name
                    and (
                        not item_name
                        or item_name == _normalize_requirement_text(str(card.subject or ""))
                        or item_name == default_item_name_text
                    )
                ):
                    item_name = dash_mid_item_name
                if build_source != "본문":
                    item_name = _normalize_table_item_name_with_card_title(
                        item_name,
                        card_title=card_title_text,
                        default_item_name=default_item_name_text,
                        card_requirement=str(card.requirement or ""),
                        section_title=section_title_text,
                        part_text=part_text,
                    )
                requirement, detail_requirement = _flatten_body_requirement_for_save(
                    item_name,
                    requirement,
                    detail_requirement,
                    build_source=build_source,
                    special_rule_applied=special_rule_applied,
                )
                described_build_method, applied_rule = _describe_build_method(
                    build_method,
                    build_source,
                    item_name=item_name,
                    requirement=requirement,
                    detail_requirement=detail_requirement,
                )
                display_build_method = described_build_method
                if build_source == "표" and table_rule_branch:
                    display_build_method = f"{described_build_method} / 분기:{table_rule_branch}"
                source_detail_label = _build_source_detail_label(
                    build_source,
                    described_build_method,
                    item_name=item_name,
                    requirement=requirement,
                    detail_requirement=detail_requirement,
                    source_hint=str(item.get("build_source_detail") or ""),
                )
                is_box_style_three_col = described_build_method.startswith(
                    "룰 기반(3단 표-박스형 본문)"
                )
                header_like_row = _is_header_like_requirement_row(
                    item_name, requirement, detail_requirement, result_note
                )
                if (
                    is_box_style_three_col
                    and detail_requirement
                    and item_name
                    and requirement
                    and item_name == requirement
                ):
                    header_like_row = False
                if (
                    not item_name
                    or not requirement
                    or header_like_row
                    or (
                        not disable_dedup
                        and _is_redundant_same_text_requirement_row(
                            item_name, requirement, detail_requirement
                        )
                    )
                ):
                    continue
                save_preview_rows.append(
                    {
                        "block_name": block_name,
                        "item_name": item_name,
                        "requirement": requirement,
                        "detail_requirement": detail_requirement,
                        "result_note": result_note,
                        "build_method": display_build_method,
                        "build_source_detail": source_detail_label,
                        "applied_rule": applied_rule,
                    }
                )
                section_rows.append(
                    {
                        "블럭명": block_name,
                        "요구사항 ID": "",
                        "항목명": item_name,
                        "요구사항": requirement,
                        "상세요건": detail_requirement,
                        "추가정보": result_note,
                        "페이지": card.page_idx if isinstance(card.page_idx, int) else "",
                        "Part": str(getattr(card, "part", None) or "").strip(),
                        "Section": str(getattr(card, "section", None) or "").strip(),
                        "Category": str(getattr(card, "category", None) or "").strip(),
                        "생성 출처": source_detail_label,
                        "생성 방식": display_build_method,
                        "표 분기": table_rule_branch,
                        "_id_prefix_hint": _normalize_requirement_text(
                            str(item.get("id_prefix_hint") or "")
                        ),
                        "_id_title_hint": _normalize_requirement_text(
                            str(item.get("id_title_hint") or "")
                        ),
                        "_card_no": str(getattr(card, "card_no", None) or ""),
                        "_inherit_prev_table_prefix": inherit_prev_table_prefix,
                    }
                )
            current_debug_row["saved_row_count"] = len(save_preview_rows)
            if not save_preview_rows:
                current_debug_row["filtered_out_reason"] = (
                    "normalize/merge 이후 살아남은 행이 없음"
                    if not card_rows
                    else "헤더/중복/동일문구 필터에 의해 제거됨"
                )
            current_debug_row["elapsed_seconds"] = round(time.time() - t0, 4)
            current_debug_row["llm_called"] = bool(card_raw == "LLM" or item_name_llm_called)
            previous_section_card = card

        _progress(f"요구사항 ID 생성 중: {section_name}")
        prefix_counters: dict[str, int] = {}
        last_row_prefix = ""
        for row in section_rows:
            inherit_prev_table_prefix = bool(row.get("_inherit_prev_table_prefix"))
            id_source = _select_requirement_id_source(
                str(row.get("항목명") or ""),
                str(row.get("요구사항") or ""),
                str(row.get("Category") or row.get("카테고리") or ""),
                section_name,
                str(row.get("_id_prefix_hint") or ""),
                str(row.get("_id_title_hint") or ""),
            )
            category_text = str(row.get("Category") or row.get("카테고리") or "")
            if inherit_prev_table_prefix and last_row_prefix:
                row_prefix = last_row_prefix
            else:
                row_prefix = _row_requirement_id_prefix(
                    id_source, category_text, section_name, prefix
                )
            prefix_counters[row_prefix] = prefix_counters.get(row_prefix, 0) + 1
            row["요구사항 ID"] = f"{row_prefix}_{prefix_counters[row_prefix]:03d}"
            last_row_prefix = row_prefix
            row.pop("_id_prefix_hint", None)
            row.pop("_id_title_hint", None)
            row.pop("_card_no", None)
            row.pop("_inherit_prev_table_prefix", None)
        if section_rows:
            section_tables[section_name] = section_rows

    _progress("결과 정리 완료")
    return _normalize_section_requirement_tables(section_tables), debug_rows
