from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import streamlit as st


def render_step3(
    *,
    build_llm_deep_schema_from_html: Callable[[str, str], tuple[list[dict], dict]],
    build_deep_cards_from_schema: Callable[..., list],
    build_sections_from_llm_schema_rule_based: Callable[[str, list[dict]], list],
    build_rfp_cards: Callable[[list], list],
    persist_llm_cost_state: Callable[[float, list[dict]], None],
    mark_running: Callable[[str, str], None],
    mark_done: Callable[[str, str], None],
    mark_error: Callable[[str, str], None],
    render_cards: Callable[[list], None],
) -> None:
    st.subheader("Table 항목 기반 조견표 생성")
    st.write("HTML에서 카드 후보를 직접 생성하고, 그 후보 기준으로 카드를 만듭니다.")

    html = st.session_state.get("html_merged_raw") or st.session_state.get("html_raw") or st.session_state.get("html") or ""
    if not html:
        st.info("이 단계는 1단계 완료 후 사용할 수 있습니다.")
        return

    deep_rows = st.session_state.get("llm_deep_schema_rows") or []
    if deep_rows:
        st.markdown("**LLM 카드 후보 (최근 생성본)**")
        st.dataframe(deep_rows, use_container_width=True, hide_index=True)
        with st.expander("LLM 카드 후보 JSON 보기", expanded=False):
            st.json(deep_rows)
    else:
        st.caption("먼저 LLM 카드 후보 생성을 실행하세요.")

    cols = st.columns(2)
    if cols[0].button("LLM 카드 후보 생성", type="primary", use_container_width=True):
        try:
            mark_running("step3", "LLM이 카드 후보를 생성하는 중")
            rows, usage = build_llm_deep_schema_from_html(
                html,
                model_name=st.session_state.get("llm_model", "gpt-4o"),
            )
            st.session_state["llm_cost_total_usd"] += usage["cost_usd"]
            st.session_state["llm_cost_logs"].append(usage)
            persist_llm_cost_state(st.session_state["llm_cost_total_usd"], st.session_state["llm_cost_logs"])
            st.session_state["llm_deep_schema_rows"] = rows
            st.success(
                f"카드 후보 {len(rows)}건 생성 완료 | "
                f"이번 호출 비용: ${usage['cost_usd']:.6f}"
            )
        except Exception as exc:  # noqa: BLE001
            mark_error("step3", str(exc))
            st.exception(exc)

    if cols[1].button("카드 후보 기반 카드 생성", type="secondary", use_container_width=True):
        try:
            mark_running("step3", "카드 후보 기반 카드를 생성하는 중")
            rows = st.session_state.get("llm_deep_schema_rows") or []
            if not rows:
                st.warning("먼저 카드 후보를 생성해 주세요.")
            else:
                cards = build_deep_cards_from_schema(
                    html=html,
                    deep_schema_rows=rows,
                    toc_items=st.session_state.get("saved_toc_items") or [],
                    build_sections_from_llm_schema_rule_based=build_sections_from_llm_schema_rule_based,
                    build_rfp_cards=build_rfp_cards,
                )
                st.session_state["cards_step3"] = cards
                mark_done("step3", f"조견표 카드 {len(cards)}건 생성 완료")
                st.success(f"카드 {len(cards)}건 생성했습니다.")
        except Exception as exc:  # noqa: BLE001
            mark_error("step3", str(exc))
            st.exception(exc)

    schema_file = Path("artifacts") / Path(st.session_state.get("file_name") or "document").stem / "llm_deep_schema_rows.json"
    io_cols = st.columns(2)
    if io_cols[0].button("카드 후보 저장", use_container_width=True):
        try:
            payload = st.session_state.get("llm_deep_schema_rows") or []
            schema_file.parent.mkdir(parents=True, exist_ok=True)
            schema_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            st.success(f"카드 후보를 저장했습니다: {schema_file}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"카드 후보 저장 실패: {exc}")
    if io_cols[1].button("카드 후보 불러오기", use_container_width=True):
        try:
            if not schema_file.exists():
                st.warning(f"저장된 카드 후보 파일이 없습니다: {schema_file}")
            else:
                loaded = json.loads(schema_file.read_text(encoding="utf-8"))
                if not isinstance(loaded, list):
                    raise ValueError("카드 후보 파일 형식이 올바르지 않습니다.")
                st.session_state["llm_deep_schema_rows"] = loaded
                st.success(f"카드 후보를 불러왔습니다: {schema_file} ({len(loaded)}건)")
                st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"카드 후보 불러오기 실패: {exc}")

    st.download_button(
        "카드 후보 JSON 다운로드",
        data=json.dumps(st.session_state.get("llm_deep_schema_rows") or [], ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"{Path(st.session_state.get('file_name') or 'document').stem}.card-candidates.json",
        mime="application/json",
        use_container_width=True,
    )

    render_cards(st.session_state["cards_step3"] or [])
