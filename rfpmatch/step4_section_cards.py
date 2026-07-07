from __future__ import annotations

from pathlib import Path

import streamlit as st

from rfpmatch.shared_io import current_artifact_root
from rfpmatch.shared_io import render_safe_table
from rfpmatch.step456_shared import _cards_to_workbook_bytes, _run_step3
from rfpmatch.step_env import StepEnv


def render_step4(env: StepEnv) -> None:
    st.subheader("4. HTML 문서 섹션 분리 카드 생성")
    st.write("확정한 최종 목차를 기준으로 선택한 HTML 소스에서 섹션 단위 카드를 생성합니다.")
    st.caption(f"현재 artifact root: {current_artifact_root()}")
    desired_source = "html_raw_merged_empty_up_postprocessed" if str(st.session_state.get("step4_html_source_mode") or "merged").strip().lower() != "raw" else "html_raw"
    generated_source = st.session_state.get("step4_generated_html_source") or ""

    st.caption(f"현재 선택된 4단계 HTML 소스: `{desired_source}`")
    if generated_source:
        st.caption(f"마지막 카드 생성에 사용된 HTML 소스: `{generated_source}`")
        if generated_source != desired_source:
            st.warning("2단계 선택이 바뀌었습니다. 현재 카드/디버그는 이전 소스 기준일 수 있으니 4단계 `카드 생성`을 다시 실행해주세요.")

    if st.session_state["saved_toc_items"] is None:
        st.info("이 단계는 3단계에서 `최종 목차 저장` 후 사용할 수 있습니다.")
        return

    if st.button("카드 생성", type="primary", use_container_width=True, key="run_step4_cards"):
        try:
            _run_step3(
                env.keep_artifacts,
                mark_running=env.mark_running,
                mark_done=env.mark_done,
                step_key="step4",
            )
            st.session_state["cards_step2_split"] = None
        except Exception as exc:  # noqa: BLE001
            env.mark_error("step4", str(exc))
            st.exception(exc)

    cards = st.session_state.get("cards_step2") or []
    hide_generated_cards = st.checkbox(
        "생성된 카드 숨기기",
        value=bool(st.session_state.get("step4_hide_generated_cards", False)),
        key="step4_hide_generated_cards",
        help="체크하면 4단계에서 생성된 카드 본문과 카드 뷰를 숨기고, 다운로드와 디버그만 먼저 볼 수 있습니다.",
    )
    if cards:
        try:
            xlsx_bytes = _cards_to_workbook_bytes(cards)
            st.download_button(
                "엑셀로 조견표 내보내기",
                data=xlsx_bytes,
                file_name=f"{Path(st.session_state.get('file_name') or 'document').stem}.cards.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        except Exception as exc:  # noqa: BLE001
            st.warning(f"엑셀 내보내기를 사용할 수 없습니다: {exc}")
    if hide_generated_cards:
        st.caption(f"생성된 카드 {len(cards)}건을 숨겼습니다.")
    else:
        env.render_cards(cards)

    match_debug = st.session_state.get("step3_toc_match_debug") or []
    if match_debug:
        matched_count = sum(1 for row in match_debug if row.get("matched"))
        unmatched_rows = [row for row in match_debug if not row.get("matched")]
        mismatched_rows = [
            row
            for row in match_debug
            if row.get("matched_index") is not None
            and row.get("resolved_index") is not None
            and row.get("matched_index") != row.get("resolved_index")
        ]
        st.markdown("**목차-본문 매칭 디버그**")
        st.caption(
            f"전체 {len(match_debug)}건 | 매칭 {matched_count}건 | 실패 {len(unmatched_rows)}건 | "
            f"예비/실제 인덱스 불일치 {len(mismatched_rows)}건"
        )
        render_safe_table(
            [
                {
                    "toc_index": row.get("toc_index"),
                    "level": row.get("level"),
                    "title": row.get("title"),
                    "matched": row.get("matched"),
                    "matched_index": row.get("matched_index"),
                    "resolved_index": row.get("resolved_index"),
                    "reason": row.get("reason"),
                    "matched_candidate": row.get("matched_candidate"),
                    "matched_block_text": row.get("matched_block_text") or row.get("matched_text"),
                }
                for row in match_debug
            ]
        )
        if mismatched_rows:
            with st.expander("예비 탐색과 실제 분리 인덱스가 다른 항목", expanded=False):
                render_safe_table(
                    [
                        {
                            "toc_index": row.get("toc_index"),
                            "level": row.get("level"),
                            "title": row.get("title"),
                            "matched_index": row.get("matched_index"),
                            "resolved_index": row.get("resolved_index"),
                            "reason": row.get("reason"),
                            "matched_candidate": row.get("matched_candidate"),
                            "matched_block_text": row.get("matched_block_text") or row.get("matched_text"),
                        }
                        for row in mismatched_rows
                    ]
                )
        if unmatched_rows:
            with st.expander("매칭 실패 항목 상세", expanded=False):
                for row in unmatched_rows:
                    st.markdown(f"**{row.get('title', '')}**")
                    st.caption(
                        f"toc_index={row.get('toc_index')} | level={row.get('level')} | "
                        f"start_from={row.get('start_from')} | reason={row.get('reason')}"
                    )
                    if row.get("all_candidate_hits"):
                        st.write("후보 히트")
                        st.json(row.get("all_candidate_hits"))
                    nearby = row.get("nearby_blocks") or []
                    if nearby:
                        st.write("주변 본문 블록")
                        st.json(nearby)
                    st.divider()
