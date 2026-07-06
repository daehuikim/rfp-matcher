from __future__ import annotations

import streamlit as st

from rfpmatch.shared_io import current_artifact_root
from rfpmatch.shared_models import RfpCard
from rfpmatch.step456_shared import _partition_card_into_table_body_segments, _partition_table_cards_by_columns
from rfpmatch.step_env import StepEnv


def render_step6(env: StepEnv) -> None:
    st.subheader("6. 부록 - 카드 분리")
    st.write("생성된 카드를 표와 본문으로 나누거나, 표 항목 기준으로 다시 분리합니다.")
    st.caption(f"현재 artifact root: {current_artifact_root()}")

    if st.session_state.get("step3_html_source"):
        st.caption(f"카드 생성용 HTML 소스: `{st.session_state['step3_html_source']}`")

    if st.session_state["saved_toc_items"] is None:
        st.info("이 단계는 3단계에서 `최종 목차 저장` 후 사용할 수 있습니다.")
        return

    split_source_cards = st.session_state.get("cards_step2") or []
    if not split_source_cards:
        st.info("먼저 4단계에서 카드 생성 결과를 만들어 주세요.")
        return

    tab_split_stage1, tab_split_table = st.tabs(["표와 본문 나누기", "표 항목으로 나누기"])

    with tab_split_stage1:
        st.write("선택한 카드를 먼저 표와 본문으로 나눕니다.")
        split_options = {
            f"{getattr(card, 'card_no', None) or card.card_id} | {card.requirement}": card
            for card in split_source_cards
        }
        option_labels = list(split_options.keys())
        default_selected = st.session_state.get("cards_step2_split_selected_ids") or []
        preselected = [label for label in option_labels if label in default_selected]
        selected_labels = st.multiselect(
            "분리할 카드 선택",
            options=option_labels,
            default=preselected or option_labels,
            key="step6_split_selected_labels",
            help="체크한 카드만 표와 본문 나누기 대상으로 보냅니다.",
        )
        selected_cards = [split_options[label] for label in selected_labels]
        st.session_state["cards_step2_split_selected_ids"] = selected_labels
        st.caption(f"선택된 카드: {len(selected_cards)} / {len(split_source_cards)}")

        if st.button("표와 본문 나누기 실행", type="primary", use_container_width=True, key="run_step6_split_stage1"):
            try:
                if not selected_cards:
                    st.warning("분리할 카드를 하나 이상 선택해 주세요.")
                    st.session_state["cards_step2_split_stage1"] = []
                    return
                stage1_cards: list[RfpCard] = []
                for card in selected_cards:
                    stage1_cards.extend(_partition_card_into_table_body_segments(card))
                st.session_state["cards_step2_split_stage1"] = stage1_cards
                st.session_state["cards_step2_split"] = None
                st.success(f"표와 본문 나누기 {len(stage1_cards)}건을 생성했습니다.")
            except Exception as exc:  # noqa: BLE001
                env.mark_error("step6", str(exc))
                st.exception(exc)

        stage1_cards = st.session_state.get("cards_step2_split_stage1") or []
        if stage1_cards:
            st.caption(f"분리 결과: {len(stage1_cards)}건")
            env.render_cards(stage1_cards)

    with tab_split_table:
        stage1_cards = st.session_state.get("cards_step2_split_stage1") or []
        if not stage1_cards:
            st.info("먼저 `표와 본문 나누기` 탭에서 1단계 분리를 실행해 주세요.")
        else:
            table_cards_source = [card for card in stage1_cards if "<table" in str(card.html_excerpt or "").lower()]
            st.caption(f"표와 본문 나누기 결과 전체 카드: {len(stage1_cards)}건, 표 카드: {len(table_cards_source)}건")
            if st.button("표 항목으로 나누기 실행", type="primary", use_container_width=True, key="run_step6_split_table"):
                try:
                    table_cards: list[RfpCard] = []
                    for card in stage1_cards:
                        table_cards.extend(_partition_table_cards_by_columns(card))
                    st.session_state["cards_step2_split_table"] = table_cards
                    st.session_state["cards_step2_split"] = None
                    st.success(f"표 항목으로 나누기 {len(table_cards)}건을 생성했습니다.")
                except Exception as exc:  # noqa: BLE001
                    env.mark_error("step6", str(exc))
                    st.exception(exc)

            table_cards = st.session_state.get("cards_step2_split_table") or []
            if table_cards:
                st.caption(f"분리 결과: {len(table_cards)}건")
                span_count = sum(1 for card in table_cards if "병합셀" in str(card.sub_subject or ""))
                if span_count:
                    st.caption(f"rowspan/colspan 포함 표: {span_count}건")
                env.render_cards(table_cards)
