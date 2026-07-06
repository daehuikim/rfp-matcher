from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from rfpmatch.shared_models import RfpCard
from rfpmatch.shared_io import current_artifact_root, render_safe_table
from rfpmatch.step456_shared import (
    _estimate_llm_cost_usd,
    _build_section_requirement_tables,
    _clear_section_requirement_cache,
    _extract_json_payload,
    _extract_token_usage,
    _format_debug_build_source_label,
    _is_trivial_single_requirement_section,
    _load_openai_api_key,
    _normalize_section_requirement_tables,
    _normalize_requirement_text,
    _responses_create_kwargs,
    _safe_sheet_name,
    _section_requirement_cards_signature,
    _section_rows_to_xlsx_bytes,
    _split_atomic_detail_units,
    _strip_trailing_orphan_bullet,
)
from rfpmatch.step_env import StepEnv


def _normalize_tab_group_level(value: str | None) -> str:
    normalized = str(value or "section").strip().lower()
    if normalized in {"part", "section", "category"}:
        return normalized
    return "section"


def _card_group_value(card: RfpCard, group_level: str) -> str:
    if group_level == "part":
        return str(getattr(card, "part", None) or "").strip() or "-"
    if group_level == "category":
        return str(getattr(card, "category", None) or getattr(card, "카테고리", None) or "").strip() or "-"
    return str(getattr(card, "section", None) or "").strip() or "-"


def _group_cards_by_selected_level(cards: list[RfpCard], group_level: str) -> list[tuple[str, list[RfpCard]]]:
    grouped: dict[str, list[RfpCard]] = {}
    order: list[str] = []
    for card in cards:
        value = _card_group_value(card, group_level)
        if value not in grouped:
            grouped[value] = []
            order.append(value)
        grouped[value].append(card)
    return [(name, grouped[name]) for name in order]


def _group_section_tables_by_selected_level(
    section_tables: dict[str, list[dict]],
    group_level: str,
) -> list[tuple[str, list[dict]]]:
    if group_level == "section":
        return [(section_name, rows) for section_name, rows in section_tables.items()]

    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for section_name, rows in section_tables.items():
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if group_level == "part":
                group_name = str(row.get("Part") or row.get("part") or "").strip()
            else:
                group_name = str(row.get("Category") or row.get("카테고리") or row.get("category") or "").strip()
            if not group_name:
                group_name = section_name
            if group_name not in grouped:
                grouped[group_name] = []
                order.append(group_name)
            grouped[group_name].append(row)
    return [(name, grouped[name]) for name in order]


def _group_section_table_entries_by_selected_level(
    section_tables: dict[str, list[dict]],
    group_level: str,
) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for section_name, rows in section_tables.items():
        for row_index, row in enumerate(rows or []):
            if not isinstance(row, dict):
                continue
            if group_level == "part":
                group_name = str(row.get("Part") or row.get("part") or "").strip()
            elif group_level == "category":
                group_name = str(row.get("Category") or row.get("카테고리") or row.get("category") or "").strip()
            else:
                group_name = str(section_name or "").strip()
            if not group_name:
                group_name = section_name
            if group_name not in grouped:
                grouped[group_name] = []
                order.append(group_name)
            grouped[group_name].append(
                {
                    "section_name": section_name,
                    "row_index": row_index,
                    "row": row,
                }
            )
    return [(name, grouped[name]) for name in order]


def _build_download_file_name(base_name: str, group_level: str) -> str:
    suffix = group_level or "section"
    return f"{base_name}.{suffix}-requirements.xlsx"


def _display_requirement_row(row: dict) -> dict:
    ordered_row: dict[str, object] = {
        "블럭명": row.get("블럭명") or row.get("block_name") or "-",
        "요구사항 ID": row.get("요구사항 ID") or "",
        "항목명": row.get("항목명") or "",
        "요구사항": row.get("요구사항") or "",
        "상세요건": row.get("상세요건") or "",
        "선택": False,
    }

    seen_keys = {"블럭명", "block_name", "요구사항 ID", "요구사항", "항목명", "상세요건"}
    for key, value in row.items():
        if key in seen_keys:
            continue
        ordered_row[key] = value
    return ordered_row


def _merge_selected_detail_rows(
    section_tables: dict[str, list[dict]],
    selected_entries: list[dict],
) -> tuple[bool, str]:
    if len(selected_entries) < 2:
        return False, "합치려는 상세요건을 2개 이상 선택해 주세요."

    comparable_fields = ["블럭명", "항목명", "요구사항", "Part", "Section", "Category", "page"]
    first_row = selected_entries[0].get("row") or {}
    mismatched_fields = [
        field
        for field in comparable_fields
        if any(str((entry.get("row") or {}).get(field) or "").strip() != str(first_row.get(field) or "").strip() for entry in selected_entries[1:])
    ]
    if mismatched_fields:
        return False, f"같은 항목/요구사항 묶음만 합칠 수 있습니다. 다른 값: {', '.join(mismatched_fields)}"

    sorted_entries = sorted(
        selected_entries,
        key=lambda entry: (str(entry.get("section_name") or ""), int(entry.get("row_index") or 0)),
    )
    detail_parts: list[str] = []
    extra_parts: list[str] = []
    seen_detail: set[str] = set()
    seen_extra: set[str] = set()
    for entry in sorted_entries:
        row = entry.get("row") or {}
        detail_text = str(row.get("상세요건") or "").strip()
        if detail_text and detail_text not in seen_detail:
            detail_parts.append(detail_text)
            seen_detail.add(detail_text)
        extra_text = str(row.get("추가정보") or "").strip()
        if extra_text and extra_text not in seen_extra:
            extra_parts.append(extra_text)
            seen_extra.add(extra_text)

    base_entry = sorted_entries[0]
    base_section_name = str(base_entry.get("section_name") or "")
    base_row_index = int(base_entry.get("row_index") or 0)
    base_rows = section_tables.get(base_section_name) or []
    if not (0 <= base_row_index < len(base_rows)) or not isinstance(base_rows[base_row_index], dict):
        return False, "기준 행을 찾지 못했습니다. 다시 실행해 주세요."

    merged_row = dict(base_rows[base_row_index])
    merged_row["상세요건"] = "\n".join(detail_parts)
    merged_row["추가정보"] = "\n".join(extra_parts)
    base_rows[base_row_index] = merged_row

    delete_targets = sorted(
        (
            (str(entry.get("section_name") or ""), int(entry.get("row_index") or 0))
            for entry in sorted_entries[1:]
        ),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    for section_name, row_index in delete_targets:
        rows = section_tables.get(section_name) or []
        if 0 <= row_index < len(rows):
            del rows[row_index]

    return True, f"{len(sorted_entries)}개 상세요건을 1개로 합쳤습니다."


def _split_selected_detail_rows(
    section_tables: dict[str, list[dict]],
    selected_entries: list[dict],
    *,
    split_mode: str = "bullet",
) -> tuple[bool, str]:
    if not selected_entries:
        return False, "분리할 상세요건을 1개 이상 선택해 주세요."

    changed_count = 0
    for entry in sorted(
        selected_entries,
        key=lambda item: (str(item.get("section_name") or ""), int(item.get("row_index") or 0)),
        reverse=True,
    ):
        section_name = str(entry.get("section_name") or "")
        row_index = int(entry.get("row_index") or 0)
        rows = section_tables.get(section_name) or []
        if not (0 <= row_index < len(rows)) or not isinstance(rows[row_index], dict):
            continue
        row = rows[row_index]
        detail_text = str(row.get("상세요건") or "").strip()
        if not detail_text:
            continue
        split_units = []
        if split_mode == "line":
            raw_units = [part.strip() for part in detail_text.splitlines() if str(part or "").strip()]
        else:
            raw_units = _split_atomic_detail_units(detail_text) or [detail_text]
        for unit in raw_units:
            cleaned = _strip_trailing_orphan_bullet(str(unit or "").strip())
            if cleaned:
                split_units.append(cleaned)
        unique_units: list[str] = []
        seen_units: set[str] = set()
        for unit in split_units:
            if unit in seen_units:
                continue
            unique_units.append(unit)
            seen_units.add(unit)
        if len(unique_units) <= 1:
            continue
        new_rows = []
        for unit in unique_units:
            new_row = dict(row)
            new_row["상세요건"] = unit
            new_rows.append(new_row)
        rows[row_index:row_index + 1] = new_rows
        changed_count += 1

    if not changed_count:
        label = "줄바꿈" if split_mode == "line" else "불릿"
        return False, f"선택한 행에서 새로 분리할 {label} 단위를 찾지 못했습니다."
    label = "줄바꿈" if split_mode == "line" else "불릿"
    return True, f"{changed_count}개 행의 상세요건을 {label} 단위로 분리했습니다."


def _push_section_requirement_history(section_tables: dict[str, list[dict]]) -> None:
    history = st.session_state.setdefault("step5_section_requirement_history", [])
    history.append(json.loads(json.dumps(section_tables, ensure_ascii=False)))
    if len(history) > 20:
        del history[:-20]


def _pop_section_requirement_history() -> dict[str, list[dict]] | None:
    history = st.session_state.get("step5_section_requirement_history") or []
    if not history:
        return None
    restored = history.pop()
    st.session_state["step5_section_requirement_history"] = history
    if not isinstance(restored, dict):
        return None
    return {str(key): value if isinstance(value, list) else [] for key, value in restored.items()}


def _apply_editor_edits(
    section_tables: dict[str, list[dict]],
    entries: list[dict],
    edited_rows: list[dict],
) -> tuple[bool, str]:
    changed = 0
    for index, edited_row in enumerate(edited_rows or []):
        if not (0 <= index < len(entries)):
            continue
        entry = entries[index]
        section_name = str(entry.get("section_name") or "")
        row_index = int(entry.get("row_index") or 0)
        rows = section_tables.get(section_name) or []
        if not (0 <= row_index < len(rows)) or not isinstance(rows[row_index], dict):
            continue
        target_row = rows[row_index]
        new_detail = str(edited_row.get("상세요건") or "").strip()
        new_extra = str(edited_row.get("추가정보") or target_row.get("추가정보") or "").strip()
        if (
            new_detail != str(target_row.get("상세요건") or "").strip()
            or new_extra != str(target_row.get("추가정보") or "").strip()
        ):
            target_row["상세요건"] = new_detail
            target_row["추가정보"] = new_extra
            changed += 1
    if not changed:
        return False, "반영할 편집 내용이 없습니다."
    return True, f"{changed}개 행의 편집 내용을 반영했습니다."


def _duplicate_selected_detail_rows(
    section_tables: dict[str, list[dict]],
    selected_entries: list[dict],
) -> tuple[bool, str]:
    if not selected_entries:
        return False, "복제할 상세요건을 1개 이상 선택해 주세요."

    duplicated = 0
    for entry in sorted(
        selected_entries,
        key=lambda item: (str(item.get("section_name") or ""), int(item.get("row_index") or 0)),
        reverse=True,
    ):
        section_name = str(entry.get("section_name") or "")
        row_index = int(entry.get("row_index") or 0)
        rows = section_tables.get(section_name) or []
        if not (0 <= row_index < len(rows)) or not isinstance(rows[row_index], dict):
            continue
        source_row = dict(rows[row_index])
        rows.insert(row_index + 1, source_row)
        duplicated += 1

    if not duplicated:
        return False, "복제할 행을 찾지 못했습니다."
    return True, f"{duplicated}개 행을 복제했습니다."


def _delete_selected_detail_rows(
    section_tables: dict[str, list[dict]],
    selected_entries: list[dict],
) -> tuple[bool, str]:
    if not selected_entries:
        return False, "삭제할 상세요건을 1개 이상 선택해 주세요."

    deleted = 0
    for entry in sorted(
        selected_entries,
        key=lambda item: (str(item.get("section_name") or ""), int(item.get("row_index") or 0)),
        reverse=True,
    ):
        section_name = str(entry.get("section_name") or "")
        row_index = int(entry.get("row_index") or 0)
        rows = section_tables.get(section_name) or []
        if not (0 <= row_index < len(rows)):
            continue
        del rows[row_index]
        deleted += 1

    if not deleted:
        return False, "삭제할 행을 찾지 못했습니다."
    return True, f"{deleted}개 행을 삭제했습니다."


def _selected_rows_payload(entries: list[dict], edited_rows: list[dict], selected_positions: list[int]) -> list[dict]:
    payload_rows: list[dict] = []
    for index in selected_positions:
        if not (0 <= index < len(entries)) or not (0 <= index < len(edited_rows)):
            continue
        entry = entries[index]
        edited_row = edited_rows[index] or {}
        source_row = entry.get("row") or {}
        payload_rows.append(
            {
                "블럭명": source_row.get("블럭명") or source_row.get("block_name") or "-",
                "요구사항 ID": source_row.get("요구사항 ID") or "",
                "항목명": _normalize_requirement_text(str(edited_row.get("항목명") or source_row.get("항목명") or "")),
                "요구사항": _normalize_requirement_text(str(edited_row.get("요구사항") or source_row.get("요구사항") or "")),
                "상세요건": _normalize_requirement_text(str(edited_row.get("상세요건") or source_row.get("상세요건") or "")),
                "추가정보": _normalize_requirement_text(str(edited_row.get("추가정보") or source_row.get("추가정보") or "")),
                "page": str(source_row.get("page") or source_row.get("Page") or "").strip(),
                "part": str(source_row.get("Part") or source_row.get("part") or "").strip(),
                "section": str(source_row.get("Section") or source_row.get("section") or "").strip(),
                "category": str(source_row.get("Category") or source_row.get("카테고리") or source_row.get("category") or "").strip(),
            }
        )
    return payload_rows


def _replace_selected_rows_via_llm(
    section_tables: dict[str, list[dict]],
    selected_entries: list[dict],
    selected_rows_payload: list[dict],
    *,
    model_name: str,
    instruction: str,
) -> tuple[bool, str, dict]:
    if not selected_entries or not selected_rows_payload:
        return False, "LLM에 보낼 선택 행이 없습니다.", {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    normalized_instruction = _normalize_requirement_text(instruction)
    if not normalized_instruction:
        return False, "LLM에게 전달할 편집 명령을 입력해 주세요.", {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    api_key = _load_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI API 키가 없습니다. 먼저 키를 저장해 주세요.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("`openai` 패키지가 필요합니다. requirements 설치를 다시 실행해주세요.") from exc

    client = OpenAI(api_key=api_key)
    first_row = selected_rows_payload[0]
    payload = {
        "instruction": normalized_instruction,
        "edit_policy": {
            "replace_selected_rows": True,
            "preserve_order_when_possible": True,
            "keep_plain_text_only": True,
            "do_not_invent_content": True,
        },
        "context": {
            "part": first_row.get("part") or "",
            "section": first_row.get("section") or "",
            "category": first_row.get("category") or "",
            "page": first_row.get("page") or "",
        },
        "selected_rows": selected_rows_payload,
    }
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": (
                    "You edit selected RFP requirement table rows according to the user's instruction. "
                    "Return JSON array only. "
                    "Each item must be an object with keys: 항목명, 요구사항, 상세요건, 추가정보. "
                    "All values must be plain text only. "
                    "Do not output markdown, explanations, or code fences. "
                    "Rewrite only within the meaning of the selected rows and user instruction. "
                    "Do not invent facts that are not supported by the selected rows. "
                    "Return the same number of items as the selected rows whenever possible, in the same order. "
                    "Only if the instruction explicitly asks to merge or split rows may the count change. "
                    "When the count changes, keep the replacement anchored at the original selected row positions as much as possible. "
                    "Preserve important bullet markers and numbering when they carry meaning. "
                    "If 추가정보 is not needed, return an empty string. "
                    "Every returned item must have a non-empty 상세요건."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        **_responses_create_kwargs(model_name),
    )
    raw_content = (getattr(response, "output_text", None) or "").strip()
    parsed = _extract_json_payload(raw_content)
    if not isinstance(parsed, list):
        raise RuntimeError("LLM 응답이 JSON 배열 형식이 아닙니다.")

    replacement_rows: list[dict] = []
    base_entry = selected_entries[0]
    base_section_name = str(base_entry.get("section_name") or "")
    base_row_index = int(base_entry.get("row_index") or 0)
    base_rows = section_tables.get(base_section_name) or []
    if not (0 <= base_row_index < len(base_rows)) or not isinstance(base_rows[base_row_index], dict):
        return False, "기준 행을 찾지 못했습니다. 다시 실행해 주세요.", {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    base_row = dict(base_rows[base_row_index])

    for item in parsed:
        if not isinstance(item, dict):
            continue
        detail = _normalize_requirement_text(str(item.get("상세요건") or "").strip())
        if not detail:
            continue
        new_row = dict(base_row)
        new_row["항목명"] = _normalize_requirement_text(str(item.get("항목명") or base_row.get("항목명") or "").strip())
        new_row["요구사항"] = _normalize_requirement_text(str(item.get("요구사항") or base_row.get("요구사항") or "").strip())
        new_row["상세요건"] = detail
        new_row["추가정보"] = _normalize_requirement_text(str(item.get("추가정보") or "").strip())
        replacement_rows.append(new_row)

    if not replacement_rows:
        return False, "LLM 결과에서 반영할 상세요건 행을 만들지 못했습니다.", {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    sorted_entries = sorted(
        selected_entries,
        key=lambda entry: (str(entry.get("section_name") or ""), int(entry.get("row_index") or 0)),
    )
    if len(replacement_rows) < len(sorted_entries):
        replacement_rows.extend(dict(base_row) for _ in range(len(sorted_entries) - len(replacement_rows)))
    if len(replacement_rows) > len(sorted_entries):
        replacement_rows = replacement_rows[: len(sorted_entries)]

    for entry, replacement_row in zip(sorted_entries, replacement_rows, strict=False):
        section_name = str(entry.get("section_name") or "")
        row_index = int(entry.get("row_index") or 0)
        rows = section_tables.get(section_name) or []
        if not (0 <= row_index < len(rows)) or not isinstance(rows[row_index], dict):
            continue
        original_row = dict(rows[row_index])
        original_row["항목명"] = _normalize_requirement_text(str(replacement_row.get("항목명") or original_row.get("항목명") or "").strip())
        original_row["요구사항"] = _normalize_requirement_text(str(replacement_row.get("요구사항") or original_row.get("요구사항") or "").strip())
        original_row["상세요건"] = _normalize_requirement_text(str(replacement_row.get("상세요건") or original_row.get("상세요건") or "").strip())
        original_row["추가정보"] = _normalize_requirement_text(str(replacement_row.get("추가정보") or original_row.get("추가정보") or "").strip())
        rows[row_index] = original_row

    input_tokens, output_tokens = _extract_token_usage(response)
    usage = {
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, input_tokens, output_tokens),
    }
    return True, f"선택한 {len(selected_entries)}개 행을 LLM 결과 {len(replacement_rows)}개 행으로 대체했습니다.", usage


def _load_saved_section_requirement_outputs(artifact_root: Path) -> tuple[dict[str, list[dict]], list[dict], dict]:
    tables_path = artifact_root / "section_requirement_tables.json"
    debug_path = artifact_root / "section_requirement_debug_rows.json"
    usage_path = artifact_root / "section_requirement_usage.json"
    signature_path = artifact_root / "section_requirement_signature.txt"
    section_tables: dict[str, list[dict]] = {}
    debug_rows: list[dict] = []
    usage_info: dict = {}
    try:
        if tables_path.exists():
            loaded_tables = json.loads(tables_path.read_text(encoding="utf-8"))
            if isinstance(loaded_tables, dict):
                section_tables = {
                    str(key): value if isinstance(value, list) else []
                    for key, value in loaded_tables.items()
                }
        if debug_path.exists():
            loaded_debug = json.loads(debug_path.read_text(encoding="utf-8"))
            if isinstance(loaded_debug, list):
                debug_rows = [row for row in loaded_debug if isinstance(row, dict)]
        if usage_path.exists():
            loaded_usage = json.loads(usage_path.read_text(encoding="utf-8"))
            if isinstance(loaded_usage, dict):
                usage_info = loaded_usage
        if signature_path.exists():
            usage_info["_signature"] = signature_path.read_text(encoding="utf-8").strip()
    except json.JSONDecodeError:
        return {}, [], {}
    return section_tables, debug_rows, usage_info


def _persist_section_requirement_outputs(
    artifact_root: Path,
    section_tables: dict[str, list[dict]],
    debug_rows: list[dict],
    usage_info: dict,
) -> None:
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "section_requirement_tables.json").write_text(
        json.dumps(section_tables, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_root / "section_requirement_tables.xlsx").write_bytes(_section_rows_to_xlsx_bytes(section_tables))
    (artifact_root / "section_requirement_debug_rows.json").write_text(
        json.dumps(debug_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_root / "section_requirement_usage.json").write_text(
        json.dumps(usage_info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_root / "section_requirement_signature.txt").write_text(
        str(usage_info.get("_signature") or ""),
        encoding="utf-8",
    )


def _record_manual_llm_usage(usage_info: dict, llm_usage: dict, *, action: str, instruction: str) -> dict:
    updated_usage = dict(usage_info or {})
    logs = list(updated_usage.get("manual_edit_logs") or [])
    logs.append(
        {
            "action": action,
            "instruction": instruction,
            "model": llm_usage.get("model"),
            "input_tokens": int(llm_usage.get("input_tokens", 0) or 0),
            "output_tokens": int(llm_usage.get("output_tokens", 0) or 0),
            "cost_usd": float(llm_usage.get("cost_usd", 0.0) or 0.0),
        }
    )
    updated_usage["manual_edit_logs"] = logs[-20:]
    return updated_usage


def _llm_debug_state_key(group_level: str, group_name: str) -> str:
    return f"step5_llm_edit_debug_{group_level}_{group_name}"


def render_step5(env: StepEnv) -> None:
    st.subheader("5. 카드 단위 조견표 생성")
    st.write("생성된 카드를 기준으로 카드 단위 조견표를 생성합니다.")
    st.caption(f"현재 artifact root: {current_artifact_root()}")

    if st.session_state.get("step3_html_source"):
        st.caption(f"카드 생성용 HTML 소스: `{st.session_state['step3_html_source']}`")

    if st.session_state["saved_toc_items"] is None:
        st.info("이 단계는 3단계에서 `최종 목차 저장` 후 사용할 수 있습니다.")
        return

    source_cards = st.session_state.get("cards_step2") or []
    if not source_cards:
        st.info("먼저 4단계에서 카드 생성 결과를 만들어 주세요.")
        return

    group_level = _normalize_tab_group_level(
        st.radio(
            "탭 기준 레벨",
            options=["part", "section", "category"],
            format_func=lambda value: {"part": "Part", "section": "Section", "category": "Category"}.get(value, value),
            index=["part", "section", "category"].index(
                _normalize_tab_group_level(st.session_state.get("step5_section_requirement_group_level"))
            ),
            key="step5_section_requirement_group_level",
            horizontal=True,
        )
    )
    grouped_cards = _group_cards_by_selected_level(source_cards, group_level)
    grouped_name_to_cards = {group_name: cards for group_name, cards in grouped_cards}
    grouped_names = list(grouped_name_to_cards.keys())
    grouped_option_map = {f"{group_name} ({len(cards)}건)": group_name for group_name, cards in grouped_cards}
    grouped_option_labels = list(grouped_option_map.keys())
    default_selected_sections = st.session_state.get("cards_step2_section_selected_names") or grouped_names
    default_selected_labels = [
        option_label
        for option_label, section_name in grouped_option_map.items()
        if section_name in default_selected_sections
    ]
    selected_section_names = st.multiselect(
        "정리할 탭 선택",
        options=grouped_option_labels,
        default=default_selected_labels or grouped_option_labels,
        key=f"step5_section_requirement_selected_names_{group_level}",
        help="선택한 탭만 카드 단위 조견표 생성 대상으로 사용합니다.",
    )
    resolved_selected_section_names = [
        grouped_option_map[label]
        for label in selected_section_names
        if label in grouped_option_map
    ]
    st.session_state["cards_step2_section_selected_names"] = resolved_selected_section_names
    st.checkbox(
        "조견표 항목 완전 중복 제거 하지 않기",
        value=bool(st.session_state.get("step5_section_requirement_disable_dedup", False)),
        key="step5_section_requirement_disable_dedup",
        help="체크하면 같은 행이 카드 간/카드 내부에서 반복되어도 그대로 남깁니다.",
    )
    selected_cards: list[RfpCard] = []
    for section_name in resolved_selected_section_names:
        selected_cards.extend(grouped_name_to_cards.get(section_name, []))
    current_signature = _section_requirement_cards_signature(selected_cards)
    cached_signature = st.session_state.get("cards_step2_section_requirement_signature")
    if cached_signature != current_signature and st.session_state.get("cards_step2_section_requirement_tables") is None:
        artifact_root = current_artifact_root()
        section_tables, debug_rows, usage_info = _load_saved_section_requirement_outputs(artifact_root)
        if section_tables:
            section_tables = _normalize_section_requirement_tables(section_tables)
            st.session_state["cards_step2_section_requirement_tables"] = section_tables
            st.session_state["cards_step2_section_requirement_debug"] = debug_rows
            st.session_state["cards_step2_section_requirement_usage"] = usage_info
            st.session_state["cards_step2_section_requirement_signature"] = cached_signature or current_signature
            st.caption("마지막 실행 결과를 아티팩트에서 복원했습니다.")
    st.caption(
        f"탭 기준: {group_level.title()} | 전체 탭 수: {len(grouped_cards)}개 | 선택 탭 수: {len(resolved_selected_section_names)}개 | "
        f"선택 카드 수: {len(selected_cards)}개"
    )

    if st.button("카드 단위 조견표 생성 실행", type="primary", use_container_width=True, key="run_step5_section_items"):
        if not selected_cards:
            st.warning("정리할 섹션을 하나 이상 선택해 주세요.")
            return
        model_name = st.session_state.get("llm_model", "gpt-4o")
        try:
            section_tables, debug_rows, usage_info = _build_section_requirement_tables(
                selected_cards,
                model_name,
                use_llm=bool(st.session_state.get("step5_use_llm", False)),
                disable_dedup=bool(st.session_state.get("step5_section_requirement_disable_dedup", False)),
            )
            section_tables = _normalize_section_requirement_tables(section_tables)
            st.session_state["cards_step2_section_requirement_tables"] = section_tables
            st.session_state["cards_step2_section_requirement_debug"] = debug_rows
            st.session_state["cards_step2_section_requirement_usage"] = usage_info
            st.session_state["cards_step2_section_requirement_signature"] = current_signature
            st.session_state["step5_section_requirement_history"] = []
            st.session_state["llm_cost_total_usd"] = float(st.session_state.get("llm_cost_total_usd", 0.0) or 0.0) + float(
                usage_info.get("cost_usd", 0.0) or 0.0
            )
            st.session_state.setdefault("llm_cost_logs", []).append(usage_info)
            if env.keep_artifacts and st.session_state.get("file_name"):
                _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
            env.mark_done("step5", f"선택 섹션 {len(section_tables)}개 / 총 {sum(len(rows) for rows in section_tables.values())}건")
        except Exception as exc:  # noqa: BLE001
            env.mark_error("step5", str(exc))
            st.exception(exc)

    section_tables = st.session_state.get("cards_step2_section_requirement_tables") or {}
    if section_tables:
        section_tables = _normalize_section_requirement_tables(section_tables)
        st.session_state["cards_step2_section_requirement_tables"] = section_tables
    usage_info = st.session_state.get("cards_step2_section_requirement_usage") or {}
    if section_tables:
        if env.keep_artifacts and st.session_state.get("file_name"):
            debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
            _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
        if cached_signature != current_signature:
            st.caption("마지막 실행 결과를 유지 중입니다. 카드가 바뀌었다면 다시 실행해 주세요.")
        hide_generated_results = st.checkbox(
            "생성된 조견표 숨기기",
            value=bool(st.session_state.get("step5_section_requirement_hide_generated_results", False)),
            key="step5_section_requirement_hide_generated_results",
            help="체크하면 조견표 본문과 다운로드를 숨기고 디버그 로그를 먼저 볼 수 있습니다.",
        )
        if not hide_generated_results:
            all_result_group_entries = _group_section_table_entries_by_selected_level(section_tables, group_level)
            all_result_groups = [
                (group_name, [entry.get("row") or {} for entry in entries])
                for group_name, entries in all_result_group_entries
            ]
            result_groups = [
                (group_name, rows)
                for group_name, rows in all_result_groups
                if not _is_trivial_single_requirement_section(rows)
            ]
            result_group_entries = [
                (group_name, entries)
                for group_name, entries in all_result_group_entries
                if not _is_trivial_single_requirement_section([entry.get("row") or {} for entry in entries])
            ]
            st.caption(f"생성 결과: {len(result_groups)}개 | 총 행 수: {sum(len(rows) for _, rows in result_groups)}개")
            if usage_info:
                st.caption(
                    f"LLM 사용량: model={usage_info.get('model')} | in={usage_info.get('input_tokens', 0)} | "
                    f"out={usage_info.get('output_tokens', 0)} | cost=${usage_info.get('cost_usd', 0.0):.6f}"
                )
            grouped_section_tables = {group_name: rows for group_name, rows in result_groups}
            xlsx_bytes = _section_rows_to_xlsx_bytes(grouped_section_tables)
            download_base_name = Path(st.session_state.get("file_name") or "document").stem
            download_file_name = _build_download_file_name(download_base_name, group_level)
            st.download_button(
                "카드 단위 조견표 엑셀 다운로드",
                data=xlsx_bytes,
                file_name=download_file_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="download_step5_section_requirements_xlsx",
            )
            hidden_group_count = len(all_result_groups) - len(result_groups)
            if hidden_group_count:
                st.caption(f"항목명=요구사항=상세요건 1행 결과 {hidden_group_count}개는 탭에서 숨겼습니다.")
            section_tab_labels = [
                _safe_sheet_name(f"{group_level.title()}: {name}", f"{group_level.title()}{idx}")
                for idx, (name, _) in enumerate(result_groups, start=1)
            ]
            section_tab_map = {
                label: group_name for label, (group_name, _) in zip(section_tab_labels, result_groups, strict=False)
            }
            result_tab_key = f"step5_section_requirement_result_tab_{group_level}"
            default_section_tab = st.session_state.get(result_tab_key)
            if default_section_tab not in section_tab_map:
                default_section_tab = section_tab_labels[0] if section_tab_labels else ""
                if default_section_tab:
                    st.session_state[result_tab_key] = default_section_tab
            st.caption("생성 결과 탭")
            selected_section_tab = st.radio(
                "생성 결과 탭",
                options=section_tab_labels,
                index=section_tab_labels.index(default_section_tab) if default_section_tab in section_tab_labels else 0,
                key=result_tab_key,
                horizontal=True,
                label_visibility="collapsed",
            )
            if selected_section_tab in section_tab_map:
                group_name = section_tab_map[selected_section_tab]
                entries = next((entries for name, entries in result_group_entries if name == group_name), [])
                rows = [entry.get("row") or {} for entry in entries]
                st.caption(f"{group_level.title()}: {group_name} | {len(rows)}건")
                st.caption("상세요건은 표에서 바로 편집할 수 있고, 아래 버튼으로 합침/분리/되돌리기를 실행할 수 있습니다.")
                editor_key = f"step5_requirement_merge_editor_{group_level}_{group_name}"
                editor_rows = [_display_requirement_row(row) for row in rows]
                editor_columns = list(editor_rows[0].keys()) if editor_rows else []
                edited_rows = st.data_editor(
                    editor_rows,
                    use_container_width=True,
                    hide_index=True,
                    disabled=[column for column in editor_columns if column not in {"선택", "상세요건", "추가정보"}],
                    column_order=editor_columns,
                    column_config={
                        "선택": st.column_config.CheckboxColumn("선택"),
                    },
                    key=editor_key,
                )
                selected_positions = [index for index, row in enumerate(edited_rows) if bool(row.get("선택"))]
                selected_entries = [entries[index] for index in selected_positions if 0 <= index < len(entries)]
                llm_instruction_key = f"step5_llm_edit_instruction_{group_level}_{group_name}"
                llm_select_all_key = f"step5_llm_edit_select_all_{group_level}_{group_name}"
                llm_select_all = st.checkbox(
                    "전체 선택해서 LLM에 전달",
                    value=bool(st.session_state.get(llm_select_all_key, False)),
                    key=llm_select_all_key,
                    help="체크하면 현재 탭의 모든 행을 LLM 편집 명령에 전달합니다. 표의 개별 선택 상태는 그대로 유지됩니다.",
                )
                llm_filter_field_key = f"step5_llm_edit_filter_field_{group_level}_{group_name}"
                llm_filter_values_key = f"step5_llm_edit_filter_values_{group_level}_{group_name}"
                llm_filter_field = st.selectbox(
                    "값 필터 기준",
                    options=["항목명", "요구사항"],
                    index=["항목명", "요구사항"].index(str(st.session_state.get(llm_filter_field_key) or "항목명")),
                    key=llm_filter_field_key,
                    help="선택한 칼럼의 값으로 여러 행을 한 번에 고를 수 있습니다.",
                )
                filter_value_options: list[str] = []
                for row in rows:
                    value = str(row.get(llm_filter_field) or "").strip()
                    if value and value not in filter_value_options:
                        filter_value_options.append(value)
                llm_filter_values = st.multiselect(
                    "값 필터 선택",
                    options=filter_value_options,
                    default=st.session_state.get(llm_filter_values_key) or [],
                    key=llm_filter_values_key,
                    help="여기에 고른 값과 일치하는 행들을 LLM 대상으로 잡습니다.",
                )
                llm_instruction = st.text_area(
                    "선택 행 LLM 편집 명령",
                    value=str(st.session_state.get(llm_instruction_key) or ""),
                    key=llm_instruction_key,
                    height=80,
                    placeholder="예: 상세요건을 불릿단위 문장으로 깔끔하게 정리해줘 / 항목명, 요구사항 레벨을 정리해줘",
                ).strip()
                llm_debug_key = _llm_debug_state_key(group_level, group_name)
                filter_selected_positions = [
                    index
                    for index, row in enumerate(rows)
                    if str(row.get(llm_filter_field) or "").strip() in set(llm_filter_values)
                ]
                if llm_select_all:
                    effective_selected_entries = entries
                    effective_selected_positions = list(range(len(entries)))
                    selection_source = "전체 선택"
                elif llm_filter_values:
                    effective_selected_positions = filter_selected_positions
                    effective_selected_entries = [
                        entries[index] for index in effective_selected_positions if 0 <= index < len(entries)
                    ]
                    selection_source = f"{llm_filter_field} 값 필터"
                else:
                    effective_selected_entries = selected_entries
                    effective_selected_positions = selected_positions
                    selection_source = "체크박스 선택"
                st.caption(
                    f"LLM 편집 준비 상태: 선택 {len(effective_selected_entries)}건"
                    + f" ({selection_source})"
                    + f" | 명령 {len(llm_instruction)}자"
                )
                action_col0, action_col1, action_col2, action_col3, action_col4, action_col5, action_col6 = st.columns(7)
                with action_col0:
                    if st.button("편집 반영", key=f"step5_apply_edits_{group_level}_{group_name}", use_container_width=True):
                        _push_section_requirement_history(section_tables)
                        applied, message = _apply_editor_edits(section_tables, entries, edited_rows)
                        if applied:
                            section_tables = _normalize_section_requirement_tables(section_tables)
                            st.session_state["cards_step2_section_requirement_tables"] = section_tables
                            if env.keep_artifacts and st.session_state.get("file_name"):
                                debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
                                _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
                            st.success(message)
                            st.rerun()
                        else:
                            _pop_section_requirement_history()
                            st.warning(message)
                with action_col1:
                    if st.button("상세요건 합침", key=f"step5_merge_details_{group_level}_{group_name}", use_container_width=True):
                        _push_section_requirement_history(section_tables)
                        merged, message = _merge_selected_detail_rows(section_tables, selected_entries)
                        if merged:
                            section_tables = _normalize_section_requirement_tables(section_tables)
                            st.session_state["cards_step2_section_requirement_tables"] = section_tables
                            if env.keep_artifacts and st.session_state.get("file_name"):
                                debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
                                _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
                            st.success(message)
                            st.rerun()
                        else:
                            _pop_section_requirement_history()
                            st.warning(message)
                with action_col2:
                    if st.button("상세요건 줄바꿈단위로 분리", key=f"step5_split_detail_lines_{group_level}_{group_name}", use_container_width=True):
                        _push_section_requirement_history(section_tables)
                        split_done, message = _split_selected_detail_rows(section_tables, selected_entries, split_mode="line")
                        if split_done:
                            section_tables = _normalize_section_requirement_tables(section_tables)
                            st.session_state["cards_step2_section_requirement_tables"] = section_tables
                            if env.keep_artifacts and st.session_state.get("file_name"):
                                debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
                                _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
                            st.success(message)
                            st.rerun()
                        else:
                            _pop_section_requirement_history()
                            st.warning(message)
                with action_col3:
                    if st.button("상세요건 불릿단위로 분리", key=f"step5_split_details_{group_level}_{group_name}", use_container_width=True):
                        _push_section_requirement_history(section_tables)
                        split_done, message = _split_selected_detail_rows(section_tables, selected_entries, split_mode="bullet")
                        if split_done:
                            section_tables = _normalize_section_requirement_tables(section_tables)
                            st.session_state["cards_step2_section_requirement_tables"] = section_tables
                            if env.keep_artifacts and st.session_state.get("file_name"):
                                debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
                                _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
                            st.success(message)
                            st.rerun()
                        else:
                            _pop_section_requirement_history()
                            st.warning(message)
                with action_col4:
                    if st.button("되돌리기", key=f"step5_undo_details_{group_level}_{group_name}", use_container_width=True):
                        restored_tables = _pop_section_requirement_history()
                        if restored_tables is None:
                            st.warning("되돌릴 이전 상태가 없습니다.")
                        else:
                            section_tables = _normalize_section_requirement_tables(restored_tables)
                            st.session_state["cards_step2_section_requirement_tables"] = section_tables
                            if env.keep_artifacts and st.session_state.get("file_name"):
                                debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
                                _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
                            st.success("직전 상세요건 편집 상태로 되돌렸습니다.")
                            st.rerun()
                with action_col5:
                    if st.button("복제", key=f"step5_duplicate_details_{group_level}_{group_name}", use_container_width=True):
                        _push_section_requirement_history(section_tables)
                        duplicated, message = _duplicate_selected_detail_rows(section_tables, selected_entries)
                        if duplicated:
                            section_tables = _normalize_section_requirement_tables(section_tables)
                            st.session_state["cards_step2_section_requirement_tables"] = section_tables
                            if env.keep_artifacts and st.session_state.get("file_name"):
                                debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
                                _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
                            st.success(message)
                            st.rerun()
                        else:
                            _pop_section_requirement_history()
                            st.warning(message)
                with action_col6:
                    if st.button("삭제", key=f"step5_delete_details_{group_level}_{group_name}", use_container_width=True):
                        _push_section_requirement_history(section_tables)
                        deleted, message = _delete_selected_detail_rows(section_tables, selected_entries)
                        if deleted:
                            section_tables = _normalize_section_requirement_tables(section_tables)
                            st.session_state["cards_step2_section_requirement_tables"] = section_tables
                            if env.keep_artifacts and st.session_state.get("file_name"):
                                debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
                                _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
                            st.success(message)
                            st.rerun()
                        else:
                            _pop_section_requirement_history()
                            st.warning(message)
                if st.button("선택 행 LLM 반영", key=f"step5_llm_edit_apply_{group_level}_{group_name}", use_container_width=True):
                    _push_section_requirement_history(section_tables)
                    try:
                        if not effective_selected_entries:
                            st.session_state[llm_debug_key] = {
                                "status": "blocked",
                                "reason": "no_selected_rows",
                                "selected_count": 0,
                                "instruction": llm_instruction,
                            }
                            _pop_section_requirement_history()
                            st.warning("LLM 반영할 행을 먼저 선택해 주세요.")
                            return
                        if not llm_instruction:
                            st.session_state[llm_debug_key] = {
                                "status": "blocked",
                                "reason": "empty_instruction",
                                "selected_count": len(selected_entries),
                                "instruction": llm_instruction,
                            }
                            _pop_section_requirement_history()
                            st.warning("LLM 편집 명령을 입력해 주세요.")
                            return
                        selected_payload = _selected_rows_payload(entries, edited_rows, effective_selected_positions)
                        st.session_state[llm_debug_key] = {
                            "status": "running",
                            "selected_count": len(effective_selected_entries),
                            "selected_positions": effective_selected_positions,
                            "instruction": llm_instruction,
                            "payload_preview": selected_payload[:3],
                        }
                        with st.spinner("선택 행을 LLM으로 정리하는 중입니다..."):
                            transformed, message, llm_usage = _replace_selected_rows_via_llm(
                                section_tables,
                                effective_selected_entries,
                                selected_payload,
                                model_name=st.session_state.get("llm_model", "gpt-4o"),
                                instruction=llm_instruction,
                            )
                        if transformed:
                            section_tables = _normalize_section_requirement_tables(section_tables)
                            st.session_state["cards_step2_section_requirement_tables"] = section_tables
                            usage_info = _record_manual_llm_usage(
                                usage_info,
                                llm_usage,
                                action="manual_selected_row_transform",
                                instruction=llm_instruction,
                            )
                            st.session_state["cards_step2_section_requirement_usage"] = usage_info
                            st.session_state["llm_cost_total_usd"] = float(st.session_state.get("llm_cost_total_usd", 0.0) or 0.0) + float(
                                llm_usage.get("cost_usd", 0.0) or 0.0
                            )
                            st.session_state.setdefault("llm_cost_logs", []).append(
                                {
                                    **llm_usage,
                                    "action": "manual_selected_row_transform",
                                    "instruction": llm_instruction,
                                }
                            )
                            st.session_state[llm_debug_key] = {
                                "status": "done",
                                "selected_count": len(effective_selected_entries),
                                "selected_positions": effective_selected_positions,
                                "instruction": llm_instruction,
                                "result_message": message,
                                "llm_usage": llm_usage,
                            }
                            if env.keep_artifacts and st.session_state.get("file_name"):
                                debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
                                _persist_section_requirement_outputs(current_artifact_root(), section_tables, debug_rows, usage_info)
                            st.success(
                                f"{message} | model={llm_usage.get('model')} | in={llm_usage.get('input_tokens', 0)} | "
                                f"out={llm_usage.get('output_tokens', 0)} | cost=${float(llm_usage.get('cost_usd', 0.0) or 0.0):.6f}"
                            )
                            st.rerun()
                        else:
                            st.session_state[llm_debug_key] = {
                                "status": "no_change",
                                "selected_count": len(selected_entries),
                                "selected_positions": selected_positions,
                                "instruction": llm_instruction,
                                "result_message": message,
                            }
                            _pop_section_requirement_history()
                            st.warning(message)
                    except Exception as exc:  # noqa: BLE001
                        st.session_state[llm_debug_key] = {
                            "status": "error",
                            "selected_count": len(effective_selected_entries),
                            "selected_positions": effective_selected_positions,
                            "instruction": llm_instruction,
                            "error": str(exc),
                        }
                        _pop_section_requirement_history()
                        st.exception(exc)
                llm_debug_state = st.session_state.get(llm_debug_key) or {}
                if llm_debug_state:
                    with st.expander("LLM 편집 디버그", expanded=False):
                        st.json(llm_debug_state)
        debug_rows = st.session_state.get("cards_step2_section_requirement_debug") or []
        if debug_rows:
            with st.expander("카드 단위 조견표 생성 디버그", expanded=False):
                filter_query = st.text_input(
                    "디버그 필터",
                    value=str(st.session_state.get("step5_section_requirement_debug_filter") or ""),
                    key="step5_section_requirement_debug_filter",
                    help="카드번호, 제목, 섹션, 적용룰, 생성 출처에서 검색어와 일치하는 항목만 보여줍니다.",
                ).strip()
                filter_fields = st.multiselect(
                    "필터 기준",
                    options=["card_no", "subject", "section", "build_source", "applied_rule", "special_rule_matched", "general_rule_hint", "plain_text", "html_excerpt"],
                    default=st.session_state.get("step5_section_requirement_debug_filter_fields")
                    or ["card_no", "subject", "section", "build_source", "applied_rule", "plain_text", "html_excerpt"],
                    key="step5_section_requirement_debug_filter_fields",
                )
                normalized_query = filter_query.lower()

                def _debug_row_matches(row: dict) -> bool:
                    if not normalized_query:
                        return True
                    for field in filter_fields:
                        if field == "build_source":
                            value = _format_debug_build_source_label(row.get("build_source") or "")
                        elif field == "applied_rule":
                            value = (row.get("body_rule_debug") or {}).get("applied_rule_display")
                        elif field == "special_rule_matched":
                            value = (row.get("body_rule_debug") or {}).get("special_rule_matched")
                        elif field == "general_rule_hint":
                            value = (row.get("body_rule_debug") or {}).get("general_rule_hint")
                        elif field == "html_excerpt":
                            value = row.get("html_excerpt")
                        else:
                            value = row.get(field)
                        if normalized_query in str(value or "").lower():
                            return True
                    return False

                filtered_debug_rows = [row for row in debug_rows if _debug_row_matches(row)]
                st.caption(f"디버그 표시: {len(filtered_debug_rows)} / {len(debug_rows)}건")
                render_safe_table(
                    [
                        {
                            "section": row.get("section"),
                            "card_no": row.get("card_no"),
                            "subject": row.get("subject"),
                            "build_source": _format_debug_build_source_label(row.get("build_source") or ""),
                            "applied_rule": (row.get("body_rule_debug") or {}).get("applied_rule_display"),
                            "special_rule_matched": (row.get("body_rule_debug") or {}).get("special_rule_matched"),
                            "general_rule_hint": (row.get("body_rule_debug") or {}).get("general_rule_hint"),
                            "plain_text_preview": str(row.get("plain_text") or "")[:200],
                            "html_excerpt_preview": str(row.get("html_excerpt") or "")[:200],
                        }
                        for row in filtered_debug_rows
                    ]
                )
                focus_labels = []
                label_to_row: dict[str, dict] = {}
                for row in filtered_debug_rows:
                    label = f"{row.get('card_no')} | {row.get('subject')} | {_format_debug_build_source_label(row.get('build_source') or '')}"
                    fragment_level = row.get("body_fragment_level")
                    if fragment_level:
                        label += f" | fragment={fragment_level}"
                    label += f" | saved={row.get('saved_row_count', 0)}"
                    focus_labels.append(label)
                    label_to_row[label] = row
                if focus_labels:
                    default_focus = st.session_state.get("step5_section_requirement_debug_focus")
                    if default_focus not in label_to_row:
                        default_focus = focus_labels[0]
                        st.session_state["step5_section_requirement_debug_focus"] = default_focus
                    selected_focus = st.selectbox(
                        "마지막으로 볼 행",
                        options=focus_labels,
                        index=focus_labels.index(default_focus),
                        key="step5_section_requirement_debug_focus",
                    )
                else:
                    selected_focus = ""
                for row in filtered_debug_rows:
                    label = f"{row.get('card_no')} | {row.get('subject')} | {_format_debug_build_source_label(row.get('build_source') or '')}"
                    fragment_level = row.get("body_fragment_level")
                    if fragment_level:
                        label += f" | fragment={fragment_level}"
                    label += f" | saved={row.get('saved_row_count', 0)}"
                    with st.expander(label, expanded=(label == selected_focus)):
                        left, right = st.columns(2)
                        with left:
                            st.markdown("**정규화된 행 결과**")
                            st.json(row.get("normalized_rows") or [])
                        with right:
                            st.markdown("**저장 직전 행 결과**")
                            st.json(row.get("save_preview_rows") or [])
                        st.markdown("**룰 디버그**")
                        st.json(row.get("body_rule_debug") or {})
                        if row.get("filtered_out_reason"):
                            st.info(f"비어 있는 이유: {row.get('filtered_out_reason')}")
                        with st.expander("원문/중간 데이터", expanded=False):
                            st.markdown("**처리 추적**")
                            st.json(
                                {
                                    "llm_called": row.get("llm_called"),
                                    "llm_call_count": row.get("llm_call_count"),
                                    "llm_stages": row.get("llm_stages") or [],
                                    "last_completed_stage": row.get("last_completed_stage"),
                                    "trace": row.get("trace") or [],
                                }
                            )
                            st.markdown("**Plain Text**")
                            st.code(str(row.get("plain_text") or ""), language="text")
                            st.markdown("**HTML Excerpt**")
                            st.code(str(row.get("html_excerpt") or ""), language="html")
                            st.markdown("**HTML Excerpt Preview**")
                            st.code(str(row.get("html_excerpt") or "")[:1200], language="html")
                            st.markdown("**Section Context Raw**")
                            st.code(str(row.get("context_raw") or ""), language="text")
                            st.markdown("**Card Raw**")
                            st.code(str(row.get("card_raw") or ""), language="text")
