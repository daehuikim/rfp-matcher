from __future__ import annotations

from pathlib import Path

import streamlit as st

from rfpmatch.shared_html_pipeline import merge_current_html_table_sections, postprocess_merged_html_from_raw
from rfpmatch.shared_io import current_artifact_root, load_text_file, render_lazy_html
from rfpmatch.step_env import StepEnv


def _on_step4_source_toggle() -> None:
    use_merged = bool(st.session_state.get("step4_use_merged_html_widget", True))
    st.session_state["step4_html_source_mode"] = "merged" if use_merged else "raw"
    st.session_state["step4_use_merged_html"] = use_merged
    st.session_state["step3_html_source"] = ""
    st.session_state["step4_generated_html_source"] = ""
    st.session_state["cards_step2"] = None
    st.session_state["cards_step2_split"] = None
    st.session_state["cards_step3"] = None
    st.session_state["cards_step4"] = None
    st.session_state["cards_step5"] = None
    st.session_state["llm_schema_rows"] = None
    st.session_state["llm_deep_schema_rows"] = None
    st.session_state["step3_toc_match_debug"] = []


def render_step2(env: StepEnv) -> None:
    del env
    st.subheader("2. HTML 문서 페이지 연속표 병합")
    st.write("1단계에서 생성한 HTML 문서를 기준으로 여러 페이지에 걸쳐 나뉜 연속 표를 병합하고 결과를 확인합니다.")
    st.caption(f"현재 artifact root: {current_artifact_root()}")
    html_text = st.session_state.get("html") or ""
    raw_source = st.session_state.get("html_raw") or ""
    if not raw_source.strip():
        st.info("먼저 1단계를 완료해 `html_raw`를 생성해 주세요.")
        return
    outputs = st.session_state.get("outputs") or {}
    markdown_text = load_text_file(outputs.get("markdown"))
    txt_text = load_text_file(outputs.get("txt"))
    html_merged_summary = st.session_state.get("html_merged_summary") or {}
    html_merged_summary_from_raw = st.session_state.get("html_merged_summary_from_raw") or html_merged_summary
    html_merged_summary_from_raw_postprocessed = st.session_state.get("html_merged_summary_from_raw_postprocessed") or html_merged_summary
    html_merged_summary_from_postprocessed = st.session_state.get("html_merged_summary_from_postprocessed") or html_merged_summary
    desired_use_merged = str(st.session_state.get("step4_html_source_mode") or "merged").strip().lower() != "raw"
    st.session_state["step4_use_merged_html"] = desired_use_merged
    st.session_state["step4_use_merged_html_widget"] = desired_use_merged
    st.checkbox(
        "4단계 카드 생성 시 연속표 병합 후 문서를 사용",
        key="step4_use_merged_html_widget",
        help="체크하면 4단계는 `html_raw_merged_empty_up_postprocessed`를, 체크 해제하면 `html_raw`를 사용합니다.",
        on_change=_on_step4_source_toggle,
    )
    if str(st.session_state.get("step4_html_source_mode") or "merged").strip().lower() != "raw":
        st.caption("현재 4단계 예정 소스: `html_raw_merged_empty_up_postprocessed`")
    else:
        st.caption("현재 4단계 예정 소스: `html_raw`")
    merge_cols = st.columns([1, 1, 2])
    if merge_cols[1].button("HTML 문서 연속표 병합", use_container_width=True, disabled=not bool(raw_source.strip()), key="step2_merge_raw_html_btn"):
        try:
            if merge_current_html_table_sections(raw_source, source_key="raw", source_label="HTML 문서 연속표 병합", output_suffix="from_raw"):
                postprocess_merged_html_from_raw()
                st.success("HTML 문서 기준으로 연속된 표 병합과 후처리를 완료했습니다.")
                st.rerun()
            else:
                st.info("병합할 연속 표가 없습니다.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"HTML 문서 연속표 병합 실패: {exc}")
    merge_cols[2].info("연속 표 병합 결과는 아래 뷰어에서 확인합니다.")
    merge_source = st.session_state.get("html_merged_source")
    if merge_source:
        merge_cols[0].caption(f"최근 병합 기준: {merge_source}")
    with st.expander("HTML 문서 보기", expanded=False):
        render_lazy_html(raw_source, key="step2_raw_source", height=520, empty_message="HTML 문서가 없습니다.")
    with st.expander("연속표 병합 HTML 문서 보기", expanded=False):
        raw_merged_html = st.session_state.get("html_merged_from_raw") or ""
        post_merged_html = st.session_state.get("html_raw_merged_empty_up_postprocessed") or st.session_state.get("html_merged_from_raw_postprocessed") or st.session_state.get("html_merged_from_postprocessed") or ""
        merge_view_cols = st.columns(2)
        with merge_view_cols[0]:
            st.markdown("**HTML 문서 연속표 병합 결과**")
            if raw_merged_html:
                if html_merged_summary_from_raw:
                    st.caption(
                        "병합 요약: "
                        f"표 {html_merged_summary_from_raw.get('before_tables', 0)}개 → "
                        f"{html_merged_summary_from_raw.get('after_tables', 0)}개, "
                        f"rowspan 병합 {html_merged_summary_from_raw.get('rowspan_cells', 0)}칸"
                    )
                render_lazy_html(raw_merged_html, key="step2_merged_raw", height=520, empty_message="병합본 HTML이 없습니다.")
                st.download_button("HTML 문서 연속표 병합본 다운로드", data=raw_merged_html.encode("utf-8"), file_name=f"{Path(st.session_state['file_name'] or 'document').stem}.merged.from_raw.html", mime="text/html", use_container_width=True, key="step2_download_merged_html_raw")
            else:
                st.info("HTML 문서 연속표 병합본이 아직 없습니다.")
        with merge_view_cols[1]:
            st.markdown("**병합 후 빈 칸 위칸 병합 결과**")
            if post_merged_html:
                raw_post_summary = html_merged_summary_from_raw_postprocessed or html_merged_summary_from_postprocessed
                if raw_post_summary:
                    st.caption(
                        "병합 요약: "
                        f"표 {raw_post_summary.get('before_tables', 0)}개 → "
                        f"{raw_post_summary.get('after_tables', 0)}개, "
                        f"rowspan 병합 {raw_post_summary.get('rowspan_cells', 0)}칸"
                    )
                render_lazy_html(post_merged_html, key="step2_merged_post", height=520, empty_message="후처리 HTML이 없습니다.")
                st.download_button("HTML 문서 연속표 병합 후 후처리본 다운로드", data=post_merged_html.encode("utf-8"), file_name=f"{Path(st.session_state['file_name'] or 'document').stem}.merged.from_raw_postprocessed.html", mime="text/html", use_container_width=True, key="step2_download_merged_html_postprocessed")
            else:
                st.info("표 병합본이 아직 없습니다. '연속 표 병합' 버튼을 눌러 생성하세요.")
    with st.expander("변환 문서 참고", expanded=False):
        ref_cols = st.columns(3)
        with ref_cols[0]:
            if html_text:
                st.caption("HTML 원본")
                render_lazy_html(html_text, key="step2_html_original", height=420, empty_message="HTML 원본이 없습니다.")
        with ref_cols[1]:
            if markdown_text:
                st.caption("Markdown")
                st.code(markdown_text, language="markdown")
        with ref_cols[2]:
            if txt_text:
                st.caption("TXT")
                st.code(txt_text, language="text")
