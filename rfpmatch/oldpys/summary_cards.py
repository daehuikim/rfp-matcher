from __future__ import annotations

import json
import os
import re
from html import escape
from pathlib import Path
from typing import Callable

import streamlit as st
from bs4 import BeautifulSoup

from rfpmatch.app_state import load_openai_api_key
from rfpmatch.models import RfpCard, Section, TocItem
from rfpmatch.toc_parser import extract_sections, extract_toc


LLM_MODEL_PRICING_PER_1M = {
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-5.2": {"input": 1.75, "output": 14.00},
    "gpt-5.4": {"input": 1.75, "output": 14.00},
    "gpt-5.5": {"input": 1.75, "output": 14.00},
}


def _load_openai_api_key() -> str:
    return load_openai_api_key()


def _extract_token_usage(response) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None) or 0
    output_tokens = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None) or 0
    try:
        return int(input_tokens), int(output_tokens)
    except (TypeError, ValueError):
        return 0, 0


def _estimate_llm_cost_usd(model_name: str, input_tokens: int, output_tokens: int) -> float:
    pricing = LLM_MODEL_PRICING_PER_1M.get(model_name, LLM_MODEL_PRICING_PER_1M["gpt-5.2"])
    return (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]


def _responses_create_kwargs(model_name: str) -> dict:
    kwargs: dict = {}
    if not str(model_name or "").startswith("gpt-5"):
        kwargs["temperature"] = 0
    return kwargs


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _safe_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("LLM 요약 JSON 파싱 실패")


def _section_context_stack(sections: list[Section]) -> list[dict]:
    stack: list[dict] = []
    contexts: list[dict] = []
    for section in sections:
        while stack and stack[-1]["level"] >= section.level:
            stack.pop()
        lineage = stack + [{"level": section.level, "title": section.title}]
        group = next((node["title"] for node in lineage if node["level"] == 1), section.title)
        if section.level == 1:
            parent_title = section.title
        else:
            parent_title = next((node["title"] for node in reversed(stack) if node["level"] == section.level - 1), "")
            if not parent_title:
                parent_title = next((node["title"] for node in reversed(stack) if node["level"] < section.level), section.title)
        contexts.append(
            {
                "group": group or section.title,
                "section": parent_title or section.title,
                "path": " > ".join(node["title"] for node in lineage),
            }
        )
        stack.append({"level": section.level, "title": section.title})
    return contexts


def _build_summary_html(one_line_summary: str, summary_points: list[str]) -> str:
    parts = ["<div>"]
    if one_line_summary:
        parts.append(f"<p><strong>요약</strong>: {escape(one_line_summary)}</p>")
    if summary_points:
        parts.append("<ul>")
        for point in summary_points:
            text = _normalize_text(point)
            if text:
                parts.append(f"<li>{escape(text)}</li>")
        parts.append("</ul>")
    parts.append("</div>")
    return "".join(parts)


def _summarize_section(section: Section, *, model_name: str) -> tuple[dict, dict]:
    api_key = _load_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다.")
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError("`openai` 패키지가 필요합니다. requirements 설치를 다시 실행해주세요.") from exc

    section_text = _normalize_text(section.text)
    section_html = (section.html or "").strip()
    if not section_text and not section_html:
        return {"one_line_summary": "", "summary_points": []}, {"model": model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    client = OpenAI(api_key=api_key)
    system_prompt = (
        "You are an expert at summarizing sections of RFP HTML documents in Korean. "
        "Summarize only the provided section content and do not invent facts. "
        "Return JSON object only with keys: one_line_summary(str), summary_points(list of 2 to 4 short strings). "
        "Keep the summary concise, faithful, and useful for a proposal review reader."
    )
    user_prompt = (
        "Summarize the following HTML section by its heading and content.\n"
        "Rules:\n"
        "1) Use only the supplied content.\n"
        "2) Keep the summary concise.\n"
        "3) Provide 2 to 4 bullet points in Korean.\n"
        "4) Mention tables or bullet-heavy requirements when they are important.\n"
        "5) Do not add explanations outside the JSON object.\n\n"
        f"[SECTION_TITLE]\n{section.title}\n\n"
        f"[SECTION_LEVEL]\n{section.level}\n\n"
        f"[SECTION_TEXT]\n{section_text[:12000]}\n\n"
        f"[SECTION_HTML]\n{section_html[:12000]}"
    )
    response = client.responses.create(
        model=model_name,
        input=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        **_responses_create_kwargs(model_name),
    )
    content = (getattr(response, "output_text", "") or "").strip()
    parsed = _safe_json_object(content)
    one_line_summary = _normalize_text(str(parsed.get("one_line_summary", "")))
    points = parsed.get("summary_points", [])
    if isinstance(points, str):
        points = [points]
    if not isinstance(points, list):
        points = []
    clean_points = [_normalize_text(str(point)) for point in points if _normalize_text(str(point))]
    if not one_line_summary and clean_points:
        one_line_summary = clean_points[0]
    input_tokens, output_tokens = _extract_token_usage(response)
    usage = {
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_llm_cost_usd(model_name, input_tokens, output_tokens),
    }
    return {"one_line_summary": one_line_summary, "summary_points": clean_points[:4]}, usage


def _sections_to_cards(sections: list[Section], model_name: str) -> tuple[list[RfpCard], list[dict]]:
    cards: list[RfpCard] = []
    usage_logs: list[dict] = []
    if not sections:
        return cards, usage_logs

    contexts = _section_context_stack(sections)
    total = len(sections)
    progress = st.progress(0)
    status = st.empty()
    for idx, (section, ctx) in enumerate(zip(sections, contexts), start=1):
        status.caption(f"{idx}/{total} 요약 중: {section.title}")
        progress.progress(int((idx - 1) / max(total, 1) * 100))
        summary, usage = _summarize_section(section, model_name=model_name)
        usage_logs.append(usage)
        cards.append(
            RfpCard(
                card_id=idx,
                card_no=str(idx),
                requirement=section.title,
                subject=section.title,
                sub_subject=summary.get("one_line_summary", "") or None,
                group=ctx["group"],
                section=ctx["section"],
                html_excerpt=_build_summary_html(
                    summary.get("one_line_summary", ""),
                    summary.get("summary_points", []),
                ),
                page_idx=section.page_idx,
                anchor=section.anchor,
            )
        )
    progress.progress(100)
    status.caption(f"{total}/{total} 요약 완료")
    return cards, usage_logs


def _sections_preview_rows(sections: list[Section]) -> list[dict]:
    rows: list[dict] = []
    for idx, section in enumerate(sections, start=1):
        rows.append(
            {
                "no": idx,
                "title": section.title,
                "level": section.level,
                "page": section.page_idx if section.page_idx is not None else "-",
                "text_len": len(_normalize_text(section.text)),
                "html_len": len((section.html or "").strip()),
            }
        )
    return rows


def render_step5(
    *,
    keep_artifacts: bool,
    mark_running: Callable[[str, str], None],
    mark_done: Callable[[str, str], None],
    mark_error: Callable[[str, str], None],
    render_cards: Callable[[list], None],
    persist_llm_cost_state: Callable[[float, list[dict]], None],
) -> None:
    st.subheader("목차 기반 요약")
    st.write("HTML 문서를 목차/섹션 기준으로 요약합니다. 각 섹션은 카드 형태로 표시됩니다.")

    html = st.session_state.get("html_merged_raw") or st.session_state.get("html_raw") or st.session_state.get("html") or ""
    if not html:
        st.info("이 단계는 1단계 완료 후 사용할 수 있습니다.")
        return

    toc_items = st.session_state.get("saved_toc_items") or extract_toc(html)
    sections = extract_sections(html, toc_items)
    st.caption(f"목차 {len(toc_items)}건 | 섹션 {len(sections)}건")

    preview_rows = _sections_preview_rows(sections[:80])
    if preview_rows:
        st.markdown("**요약 대상 섹션 미리보기**")
        st.dataframe(preview_rows, use_container_width=True, hide_index=True)

    if st.button("목차 기반 요약 생성", type="primary", use_container_width=True):
        try:
            mark_running("step5", "목차 기반 요약을 생성하는 중")
            cards, usage_logs = _sections_to_cards(sections, st.session_state.get("llm_model", "gpt-4o"))
            total_cost = sum(log.get("cost_usd", 0.0) for log in usage_logs)
            st.session_state["llm_cost_total_usd"] += total_cost
            st.session_state["llm_cost_logs"].extend(usage_logs)
            persist_llm_cost_state(st.session_state["llm_cost_total_usd"], st.session_state["llm_cost_logs"])
            st.session_state["cards_step5"] = cards
            if keep_artifacts:
                artifact_root = Path("artifacts") / Path(st.session_state.get("file_name") or "document").stem
                artifact_root.mkdir(parents=True, exist_ok=True)
                (artifact_root / "toc_summary_cards.json").write_text(
                    json.dumps([card.__dict__ for card in cards], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            mark_done("step5", f"목차 기반 요약 카드 {len(cards)}건 생성 완료")
            st.success(
                f"목차 기반 요약 카드 {len(cards)}건을 생성했습니다. "
                f"(이번 호출 비용: ${total_cost:.6f})"
            )
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.session_state["cards_step5"] = None
            mark_error("step5", str(exc))
            st.exception(exc)

    cards = st.session_state.get("cards_step5") or []
    if cards:
        st.download_button(
            "요약 카드 JSON 다운로드",
            data=json.dumps([card.__dict__ for card in cards], ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"{Path(st.session_state.get('file_name') or 'document').stem}.toc-summary-cards.json",
            mime="application/json",
            use_container_width=True,
        )
    render_cards(cards)
