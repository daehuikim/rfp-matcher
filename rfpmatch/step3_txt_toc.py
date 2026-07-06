from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from rfpmatch.shared_cards_export import cards_html_excerpt_merged_xlsx_bytes
from rfpmatch.shared_io import current_artifact_root, load_text_file, render_lazy_html, render_safe_table, rows_to_xlsx_bytes
from rfpmatch.shared_llm import build_llm_toc_from_raw_document
from rfpmatch.shared_toc import (
    build_page_source_map,
    drop_toc_heading_items,
    fill_missing_pages_from_neighbors,
    filter_items_with_page,
    insert_toc_item,
    reconcile_pages_from_candidates,
    refresh_step2_split_views_from_final_toc,
    relevel_items,
    trim_toc_depth,
    rows_to_toc_items,
    toc_items_to_rows,
)
from rfpmatch.step_env import StepEnv


def _selected_row_indices_from_dataframe_state(state) -> list[int]:
    if state is None:
        return []
    selection = getattr(state, "selection", None)
    if selection is None and isinstance(state, dict):
        selection = state.get("selection")
    if selection is None:
        return []
    rows = getattr(selection, "rows", None)
    if rows is None and isinstance(selection, dict):
        rows = selection.get("rows")
    if rows is None:
        return []
    if isinstance(rows, (list, tuple)):
        result: list[int] = []
        for row in rows:
            try:
                result.append(int(row))
            except (TypeError, ValueError):
                continue
        return result
    try:
        return [int(rows)]
    except (TypeError, ValueError):
        return []


def render_step3(env: StepEnv) -> None:
    st.subheader("3. TXT 문서 목차 추출")
    st.write("TXT 문서를 기준으로 최종 목차를 생성하거나 불러오고, 편집한 뒤 다음 단계에서 사용할 목차로 확정합니다.")
    st.caption(f"현재 artifact root: {current_artifact_root()}")
    if not st.session_state.get("html_raw"):
        st.info("이 단계는 1단계 완료 후 사용할 수 있습니다.")
        return
    toc_file = current_artifact_root() / "toc.json"
    toc_area_items = st.session_state.get("toc_area_items") or []
    toc_body_items = st.session_state.get("toc_body_items") or []
    html_page_candidates = toc_area_items + toc_body_items
    page_source_map = build_page_source_map(toc_area_items, toc_body_items)
    with st.container(border=True):
        st.subheader("저장된 최종 목차 불러오기")
        st.caption(f"최종 목차 파일: {toc_file}")
        load_cols = st.columns(2)
        if load_cols[0].button("최종 목차 불러오기", use_container_width=True):
            try:
                if not toc_file.exists():
                    st.session_state["toc_items"] = []
                    st.session_state["saved_toc_items"] = []
                    st.session_state["toc_editor_version"] += 1
                    st.session_state["step2_active_tab"] = "목차 추출"
                    refresh_step2_split_views_from_final_toc()
                    st.warning(f"저장된 최종 목차 파일이 없어 빈 최종 목차를 불러왔습니다: {toc_file}")
                    st.rerun()
                else:
                    loaded = json.loads(toc_file.read_text(encoding="utf-8"))
                    loaded_items = rows_to_toc_items(loaded)
                    loaded_items = reconcile_pages_from_candidates(loaded_items, html_page_candidates)
                    loaded_items = trim_toc_depth(drop_toc_heading_items(fill_missing_pages_from_neighbors(loaded_items)))
                    st.session_state["toc_items"] = loaded_items
                    st.session_state["saved_toc_items"] = loaded_items
                    st.session_state["toc_editor_version"] += 1
                    st.session_state["step2_active_tab"] = "목차 추출"
                    refresh_step2_split_views_from_final_toc()
                    st.success(f"최종 목차를 불러왔습니다: {toc_file} ({len(loaded_items)}건)")
                    st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"최종 목차 불러오기 실패: {exc}")
        if st.session_state["saved_toc_items"] is not None:
            load_cols[1].caption(f"저장본 적용됨: {len(st.session_state['saved_toc_items'])}건")
    if st.session_state.get("saved_toc_items") is not None:
        refresh_step2_split_views_from_final_toc()
    toc_area_items = st.session_state.get("toc_area_items") or []
    toc_body_items = st.session_state.get("toc_body_items") or []
    html_page_candidates = toc_area_items + toc_body_items
    saved_toc_items = st.session_state.get("saved_toc_items")
    toc_items = saved_toc_items if saved_toc_items is not None else st.session_state.get("toc_items")
    if toc_items is None:
        st.caption("아직 목차를 추출하지 않았습니다.")
        return
    if not toc_items:
        st.info("최종 목차가 비어 있습니다. 빈 최종 목차 상태로 다음 단계를 진행할 수 있습니다.")
    split_toc_html = st.session_state.get("step2_toc_html") or ""
    split_body_html = st.session_state.get("step2_body_html") or ""
    with st.expander("병합 표 HTML 분리 뷰어", expanded=False):
        st.caption("가장 먼저 병합된 표 HTML을 기준으로 TOC 영역과 본문 영역을 나눈 결과입니다.")
        split_cols = st.columns(2)
        with split_cols[0]:
            st.caption("TOC 영역 HTML")
            render_lazy_html(split_toc_html, key="step3_toc_html", height=520, empty_message="TOC 영역 HTML이 없습니다.")
        with split_cols[1]:
            st.caption("본문 영역 HTML")
            render_lazy_html(split_body_html, key="step3_body_html", height=520, empty_message="본문 영역 HTML이 없습니다.")
    outputs = st.session_state.get("outputs") or {}
    txt_text = load_text_file(outputs.get("txt"))
    with st.container(border=True):
        st.subheader("TXT 문서 기준 최종 LLM 목차 추출")
        st.caption("TXT 원문 전체를 LLM에 보내 최종 목차를 바로 생성합니다.")
        if st.button("최종 LLM 목차 추출", use_container_width=True, key="step2_txt_llm_extract"):
            try:
                tx_items, usage_info = build_llm_toc_from_raw_document(txt_text, source_name="TXT", model_name=st.session_state.get("llm_model", "gpt-4o"))
                st.session_state["txt_llm_toc_items"] = tx_items
                st.session_state["llm_cost_total_usd"] += usage_info["cost_usd"]
                st.session_state["llm_cost_logs"].append(usage_info)
                if env.persist_llm_cost_state:
                    env.persist_llm_cost_state(st.session_state["llm_cost_total_usd"], st.session_state["llm_cost_logs"])
                llm_items_page_ref, removed_count = filter_items_with_page(tx_items)
                llm_items = relevel_items(llm_items_page_ref)
                llm_items = drop_toc_heading_items(llm_items)
                llm_items = reconcile_pages_from_candidates(llm_items, html_page_candidates)
                llm_items = trim_toc_depth(fill_missing_pages_from_neighbors(llm_items))
                st.session_state["toc_items"] = llm_items
                st.session_state["saved_toc_items"] = llm_items
                st.session_state["toc_editor_version"] += 1
                refresh_step2_split_views_from_final_toc()
                st.session_state["llm_cost_last_msg"] = (
                    f"TXT 원문 기반 최종 목차 원본 {len(tx_items)}건 생성 → 페이지 없는 {removed_count}건 제거 → "
                    f"level 재조정 후 {len(llm_items)}건 확정 | "
                    f"이번 호출 비용: ${usage_info['cost_usd']:.6f}"
                )
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"최종 LLM 목차 추출 실패: {exc}")
        saved_final_toc = st.session_state.get("saved_toc_items")
        final_txt_items = saved_final_toc if saved_final_toc is not None else (st.session_state.get("txt_llm_toc_items") or [])
        st.caption(f"현재 최종 목차 건수: {len(final_txt_items)}개")
        if final_txt_items:
            render_safe_table(toc_items_to_rows(final_txt_items, page_source_map=page_source_map))
        else:
            st.info("최종 LLM 목차 추출 결과가 없습니다.")
        if st.session_state.get("llm_cost_last_msg"):
            st.success(st.session_state["llm_cost_last_msg"])
            st.session_state["llm_cost_last_msg"] = ""
    current_final_toc = st.session_state.get("saved_toc_items")
    toc_source_items = current_final_toc if current_final_toc is not None else toc_items
    edited_items = toc_source_items
    with st.container(border=True):
        st.markdown("**최종 목차 (편집 가능)**")
        st.caption(f"현재 목차 건수: {len(toc_source_items)}개")
        edited = render_safe_table(
            toc_items_to_rows(toc_source_items, page_source_map=page_source_map),
            editable=True,
            num_rows="dynamic",
            key=f"merged_toc_editor_{st.session_state['toc_editor_version']}",
        )
        rows = edited.to_dict("records") if hasattr(edited, "to_dict") else edited
        edited_items = rows_to_toc_items(rows)
        edited_items = reconcile_pages_from_candidates(edited_items, html_page_candidates)
        edited_items = trim_toc_depth(fill_missing_pages_from_neighbors(drop_toc_heading_items(edited_items)))
        st.session_state["toc_items"] = edited_items
        st.session_state["saved_toc_items"] = edited_items
        refresh_step2_split_views_from_final_toc()
        st.caption("위 표에서 내용을 직접 편집한 뒤, 아래에서 기준 행을 골라 다음 행에 추가하거나 위로 이동할 수 있습니다.")
        if edited_items:
            row_labels = [f"{idx + 1}. {item.title}" for idx, item in enumerate(edited_items)]
            selected_row_label = st.selectbox(
                "기준 행 선택",
                options=row_labels,
                index=0,
                key=f"toc_insert_after_select_{st.session_state['toc_editor_version']}",
            )
            selected_row = row_labels.index(selected_row_label)
            st.caption(f"선택된 행: {selected_row + 1}번")
            action_cols = st.columns([1, 1])
            with action_cols[0]:
                if st.button("+", use_container_width=True, key="toc_insert_selected_below_btn"):
                    edited_items = insert_toc_item(edited_items, selected_row + 1, title="새 항목")
                    edited_items = reconcile_pages_from_candidates(edited_items, html_page_candidates)
                    edited_items = trim_toc_depth(fill_missing_pages_from_neighbors(drop_toc_heading_items(edited_items)))
                    st.session_state["toc_items"] = edited_items
                    st.session_state["saved_toc_items"] = edited_items
                    st.session_state["toc_editor_version"] += 1
                    refresh_step2_split_views_from_final_toc()
                    st.rerun()
            with action_cols[1]:
                if st.button("선택 행 위로 이동", use_container_width=True, key="toc_move_row_up_btn"):
                    edited_items = move_toc_item(edited_items, selected_row, -1)
                    edited_items = reconcile_pages_from_candidates(edited_items, html_page_candidates)
                    edited_items = trim_toc_depth(fill_missing_pages_from_neighbors(drop_toc_heading_items(edited_items)))
                    st.session_state["toc_items"] = edited_items
                    st.session_state["saved_toc_items"] = edited_items
                    st.session_state["toc_editor_version"] += 1
                    refresh_step2_split_views_from_final_toc()
                    st.rerun()
    save_cols = st.columns(2)
    if save_cols[0].button("최종 목차 저장", use_container_width=True):
        try:
            toc_file.parent.mkdir(parents=True, exist_ok=True)
            toc_file.write_text(
                json.dumps(toc_items_to_rows(edited_items, page_source_map=page_source_map), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            st.success(f"최종 목차 {len(edited_items)}건을 저장했습니다. 카드 생성에서 이 저장본을 사용합니다.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"최종 목차 저장 실패: {exc}")
    if st.session_state["saved_toc_items"] is not None:
        save_cols[1].caption(f"자동 저장됨: {len(st.session_state['saved_toc_items'])}건")
    with st.container(border=True):
        st.subheader("목차/본문 분리 반영")
        executable_toc_items = st.session_state.get("saved_toc_items")
        if executable_toc_items is None:
            st.info("최종 목차 불러오기 또는 생성 후 실행하세요.")
        else:
            run_cols = st.columns(2)
            if run_cols[0].button("목차/본문 분리 실행", type="primary", use_container_width=True, key="run_step2_after_final_toc"):
                try:
                    if refresh_step2_split_views_from_final_toc():
                        st.success("최종 목차를 기준으로 목차/본문 분리를 다시 반영했습니다.")
                        st.rerun()
                    else:
                        st.warning("분리할 HTML이 없습니다.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"2단계 실행 실패: {exc}")
            run_cols[1].caption("최종 목차를 반영한 뒤 TOC 영역과 본문 영역을 다시 계산합니다.")
    with st.container(border=True):
        st.subheader("목차 결과 내보내기")
        toc_excel_cols = st.columns(2)
        try:
            xlsx_bytes = rows_to_xlsx_bytes({"목차영역": toc_items_to_rows(toc_area_items, page_source_map=page_source_map), "본문영역": toc_items_to_rows(toc_body_items, page_source_map=page_source_map), "병합": toc_items_to_rows(edited_items, page_source_map=page_source_map)})
            toc_excel_cols[0].download_button("목차 엑셀 다운로드", data=xlsx_bytes, file_name=f"{Path(st.session_state.get('file_name') or 'document').stem}.toc.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True, key="toc_excel_download_btn")
            toc_excel_cols[1].caption("목차 영역, 본문 영역, 병합 결과를 각각 시트로 내보냅니다.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"목차 엑셀 내보내기 실패: {exc}")
        cards_step2 = st.session_state.get("cards_step2") or []
        if cards_step2:
            try:
                html_excerpt_xlsx = cards_html_excerpt_merged_xlsx_bytes(cards_step2)
                st.download_button("조견표의 html_excert 내용 html로 파싱하여 엑셀로 내보내기", data=html_excerpt_xlsx, file_name=f"{Path(st.session_state.get('file_name') or 'document').stem}.cards-excerpt.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True, key="html_excerpt_excel_download_btn")
                st.caption("생성된 카드의 HTML 발췌만 모아 엑셀로 내보냅니다.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"HTML 발췌 엑셀 내보내기 실패: {exc}")
