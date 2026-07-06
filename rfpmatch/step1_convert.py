from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from rfpmatch.shared_vlm_review import TableCandidate, find_suspicious_table_candidates, has_pymupdf, review_candidate_with_vlm
from rfpmatch.shared_opendataloader_adapter import OpenDataLoaderError, locate_outputs, run_opendataloader
from rfpmatch.shared_html_pipeline import (
    build_step1_raw_html_variants,
    build_page_summary,
    build_page_summary_from_html,
    build_txt_from_html_or_markdown,
    detect_document_input_kind,
    fallback_html_from_markdown,
    is_hana_pdf_selected,
    normalize_html_document,
    strip_html_styles,
    _find_hwp_converter_command,
    write_hwpx_step1_outputs,
    write_hwp_step1_outputs,
    write_docx_step1_outputs,
)
from rfpmatch.shared_io import load_text_file, render_lazy_html, render_safe_table, save_step1_bundle
from rfpmatch.shared_io import current_artifact_root
from rfpmatch.step_env import StepEnv


def _run_step1(env: StepEnv) -> None:
    upload_name = st.session_state.get("uploaded_pdf_name")
    upload_bytes = st.session_state.get("uploaded_pdf_bytes")
    if not upload_name or not upload_bytes:
        st.warning("먼저 PDF, DOCX, HWP 또는 HWPX를 업로드해주세요.")
        return
    upload_kind = detect_document_input_kind(str(upload_name), upload_bytes)
    env.mark_running("step1", "문서를 HTML로 변환하는 중")
    workdir = Path(st.session_state["workdir"])
    input_dir = workdir / "input"
    output_dir = workdir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = input_dir / upload_name
    pdf_path.write_bytes(upload_bytes)
    st.session_state["uploaded_pdf_path"] = str(pdf_path)
    with st.spinner("문서를 HTML로 변환하는 중입니다..."):
        content_list: list[dict] = []
        if upload_kind == "docx":
            outputs = write_docx_step1_outputs(pdf_path, output_dir)
            html_path = outputs["html"]
            html = normalize_html_document(html_path.read_text(encoding="utf-8", errors="ignore"))
            markdown_path = outputs.get("markdown")
            markdown_text = markdown_path.read_text(encoding="utf-8", errors="ignore") if markdown_path and markdown_path.exists() else ""
            txt_path = outputs.get("txt")
            txt_text = txt_path.read_text(encoding="utf-8", errors="ignore") if txt_path and txt_path.exists() else ""
        elif upload_kind == "hwpx":
            try:
                outputs = write_hwpx_step1_outputs(pdf_path, output_dir)
            except Exception as exc:  # noqa: BLE001
                raise OpenDataLoaderError(f"HWPX 변환 실패: {exc}") from exc
            html_path = outputs["html"]
            html = normalize_html_document(html_path.read_text(encoding="utf-8", errors="ignore"))
            markdown_path = outputs.get("markdown")
            markdown_text = markdown_path.read_text(encoding="utf-8", errors="ignore") if markdown_path and markdown_path.exists() else ""
            txt_path = outputs.get("txt")
            txt_text = txt_path.read_text(encoding="utf-8", errors="ignore") if txt_path and txt_path.exists() else ""
        elif upload_kind == "hwp":
            if not _find_hwp_converter_command():
                raise OpenDataLoaderError(
                    "HWP 파일 변환 도구를 찾을 수 없습니다. 현재 환경에는 LibreOffice/soffice가 없어 HWP를 HTML로 변환할 수 없습니다."
                )
            try:
                outputs = write_hwp_step1_outputs(pdf_path, output_dir)
            except Exception as exc:  # noqa: BLE001
                raise OpenDataLoaderError(f"HWP 변환 실패: {exc}") from exc
            html_path = outputs["html"]
            html = normalize_html_document(html_path.read_text(encoding="utf-8", errors="ignore"))
            markdown_path = outputs.get("markdown")
            markdown_text = markdown_path.read_text(encoding="utf-8", errors="ignore") if markdown_path and markdown_path.exists() else ""
            txt_path = outputs.get("txt")
            txt_text = txt_path.read_text(encoding="utf-8", errors="ignore") if txt_path and txt_path.exists() else ""
        elif upload_kind == "doc":
            raise OpenDataLoaderError("구형 .doc 형식은 현재 환경에서 직접 변환할 수 없습니다. .docx로 저장한 뒤 다시 업로드해주세요.")
        else:
            run_opendataloader(pdf_path, output_dir, use_hybrid=False)
            outputs = locate_outputs(output_dir)
            html_path = outputs["html"]
            if html_path and html_path.exists():
                html = normalize_html_document(html_path.read_text(encoding="utf-8", errors="ignore"))
            else:
                html = fallback_html_from_markdown(outputs, content_list)
            markdown_path = outputs.get("markdown")
            markdown_text = markdown_path.read_text(encoding="utf-8", errors="ignore") if markdown_path and markdown_path.exists() else ""
            txt_path = outputs.get("txt")
            txt_text = txt_path.read_text(encoding="utf-8", errors="ignore") if txt_path and txt_path.exists() else ""
        raw_html_original = strip_html_styles(html)
        step1_variants = build_step1_raw_html_variants(
            raw_html_original,
            file_name=upload_name,
            apply_empty_column_cleanup=is_hana_pdf_selected(),
        )
        raw_html = step1_variants["html_raw"]
        st.session_state["html_raw"] = raw_html
        st.session_state["html_raw_restored"] = step1_variants["html_raw_restored"]
        st.session_state["html_raw_original"] = raw_html_original
        st.session_state["html_raw_postprocessed"] = step1_variants["html_raw_restored"]
        st.session_state["html_raw_empty_column_postprocessed"] = None
        st.session_state["html_merged_raw"] = step1_variants["html_merged_from_raw"]
        st.session_state["html_merged_from_raw"] = step1_variants["html_merged_from_raw"]
        st.session_state["html_raw_merged_empty_up_postprocessed"] = step1_variants["html_merged_from_raw_postprocessed"]
        st.session_state["html_merged_from_raw_postprocessed"] = step1_variants["html_merged_from_raw_postprocessed"]
        st.session_state["empty_column_postprocessed"] = False
        if not markdown_text.strip():
            markdown_text = ""
        if not txt_text.strip():
            generated_txt = build_txt_from_html_or_markdown(html, markdown_text)
            if generated_txt.strip():
                txt_path = output_dir / f"{pdf_path.stem}.txt"
                txt_path.write_text(generated_txt, encoding="utf-8")
                outputs["txt"] = txt_path
                txt_text = generated_txt
    st.session_state["html"] = html
    st.session_state["html_raw"] = st.session_state.get("html_raw") or raw_html
    st.session_state["html_raw_original"] = st.session_state.get("html_raw_original") or raw_html_original
    st.session_state["html_raw_restored"] = st.session_state.get("html_raw_restored") or st.session_state["html_raw"]
    st.session_state["html_merged_raw"] = st.session_state.get("html_merged_raw") or st.session_state.get("html_merged_from_raw")
    st.session_state["html_merged_from_raw"] = st.session_state.get("html_merged_from_raw") or st.session_state["html_merged_raw"]
    st.session_state["html_raw_merged_empty_up_postprocessed"] = st.session_state.get("html_raw_merged_empty_up_postprocessed") or st.session_state.get("html_merged_from_raw_postprocessed")
    st.session_state["html_merged_from_raw_postprocessed"] = st.session_state.get("html_merged_from_raw_postprocessed") or st.session_state["html_raw_merged_empty_up_postprocessed"]
    st.session_state["page_summary"] = build_page_summary(content_list) or build_page_summary_from_html(html)
    for key in (
        "toc_area_items", "toc_body_items", "toc_items", "saved_toc_items", "cards_step2",
        "cards_step2_split", "cards_step3", "html_merged", "html_merged_raw", "html_merged_from_raw",
        "html_merged_from_postprocessed", "html_merged_source", "html_merged_summary",
        "html_merged_summary_from_raw", "html_merged_summary_from_postprocessed",
    ):
        st.session_state[key] = None
    st.session_state["cards_step2_split_selected_ids"] = []
    env.mark_done("step1", "HTML 문서 생성 완료")
    st.session_state["progress"]["step2"] = {"state": "pending", "message": "목차 추출 후 실행 가능"}
    st.session_state["progress"]["step3"] = {"state": "pending", "message": "HTML 변환 후 실행 가능"}
    if env.keep_artifacts:
        artifact_root = current_artifact_root()
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "document.html").write_text(html, encoding="utf-8")
        if raw_html_original.strip():
            original_raw_path = artifact_root / "document_html_raw_original.html"
            original_raw_path.write_text(raw_html_original, encoding="utf-8")
            outputs["html_raw_original"] = str(original_raw_path.resolve())
        if markdown_text.strip():
            (artifact_root / "markdown.md").write_text(markdown_text, encoding="utf-8")
        if txt_text.strip():
            (artifact_root / "txt.txt").write_text(txt_text, encoding="utf-8")
        st.success(f"1단계 산출물을 {artifact_root} 에 저장했습니다.")
    st.session_state["outputs"] = {key: str(value) if value else None for key, value in outputs.items()}
    st.success("1단계 완료.")
    st.rerun()


def render_step1(env: StepEnv) -> None:
    st.subheader("1. 문서 변환")
    st.write("업로드한 PDF, DOCX, HWP 또는 HWPX를 변환하고, 다음 단계에서 사용할 HTML 문서, Markdown 문서, TXT 문서를 생성합니다.")
    st.caption(f"현재 artifact root: {current_artifact_root()}")
    current_file = st.session_state.get("uploaded_pdf_name") or st.session_state.get("file_name") or "-"
    current_digest = st.session_state.get("file_digest") or "-"
    st.caption(f"현재 선택 문서: {current_file} | digest: {current_digest}")
    uploaded_path = st.session_state.get("uploaded_pdf_path")
    if uploaded_path:
        st.caption(f"업로드 저장 경로: {uploaded_path}")
    if st.button("1단계 실행", type="primary", use_container_width=True):
        try:
            _run_step1(env)
        except OpenDataLoaderError as exc:
            env.mark_error("step1", str(exc))
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            env.mark_error("step1", str(exc))
            st.exception(exc)
    io_cols = st.columns(2)
    if io_cols[0].button("1단계 결과 저장", use_container_width=True):
        bundle_path = save_step1_bundle(include_merge_outputs=True)
        if bundle_path:
            st.success(f"저장 완료: {bundle_path}")
        else:
            st.warning("저장할 1단계 결과가 없습니다. 먼저 변환을 실행해주세요.")
    from rfpmatch.app_state import load_last_conversion_bundle_path
    from rfpmatch.shared_io import restore_step1_bundle
    if io_cols[1].button("저장본 불러오기", use_container_width=True):
        bundle_path = load_last_conversion_bundle_path()
        if not bundle_path:
            st.warning("불러올 저장본이 없습니다.")
        elif restore_step1_bundle(bundle_path):
            st.success(f"불러오기 완료: {bundle_path}")
        else:
            st.error("저장본을 불러오지 못했습니다.")
    if st.session_state["outputs"]:
        st.caption("1단계 산출물")
        visible_outputs = {k: v for k, v in st.session_state["outputs"].items() if k not in {"json", "html"}}
        if st.session_state.get("html_raw"):
            visible_outputs["html_raw"] = "(세션 산출물, 파일 미저장)"
        st.json(visible_outputs)
    outputs = st.session_state.get("outputs") or {}
    markdown_text = load_text_file(outputs.get("markdown"))
    txt_text = load_text_file(outputs.get("txt"))
    html_text = st.session_state.get("html") or ""
    page_summary = st.session_state.get("page_summary") or []
    has_loaded_final_toc = st.session_state.get("saved_toc_items") is not None or st.session_state.get("toc_items") is not None
    if not any([html_text, markdown_text, txt_text]) and not has_loaded_final_toc:
        st.caption("아직 변환 산출물이 없습니다.")
        return
    if page_summary:
        st.markdown("**페이지 표시 (PDF 기준)**")
        render_safe_table(page_summary)
    st.caption(
        "산출물 상태: "
        f"HTML Raw {'있음' if st.session_state.get('html_raw') else '없음'} | "
        f"HTML 원본 {'있음' if html_text else '없음'} | "
        f"Markdown {'있음' if markdown_text else '없음'} | "
        f"TXT {'있음' if txt_text else '없음'}"
    )
    st.markdown("**변환 문서 다운로드**")
    dl_cols = st.columns(3)
    with dl_cols[0]:
        raw_html_text = st.session_state.get("html_raw") or html_text
        if raw_html_text:
            st.download_button("HTML 다운로드", data=raw_html_text.encode("utf-8"), file_name=f"{Path(st.session_state['file_name'] or 'document').stem}.html_raw.html", mime="text/html", use_container_width=True, key="step1_download_html_raw")
    with dl_cols[1]:
        original_raw_html_text = st.session_state.get("html_raw_original") or ""
        if original_raw_html_text:
            st.download_button("Original HTML 다운로드", data=original_raw_html_text.encode("utf-8"), file_name=f"{Path(st.session_state['file_name'] or 'document').stem}.html_raw_original.html", mime="text/html", use_container_width=True, key="step1_download_html_raw_original")
    with dl_cols[2]:
        if markdown_text:
            st.download_button("Markdown 다운로드", data=markdown_text.encode("utf-8"), file_name=f"{Path(st.session_state['file_name'] or 'document').stem}.md", mime="text/markdown", use_container_width=True, key="step1_download_markdown")
    dl_cols_2 = st.columns(1)
    with dl_cols_2[0]:
        if txt_text:
            st.download_button("TXT 다운로드", data=txt_text.encode("utf-8"), file_name=f"{Path(st.session_state['file_name'] or 'document').stem}.txt", mime="text/plain", use_container_width=True, key="step1_download_txt")
    with st.expander("Original HTML 문서 보기", expanded=False):
        original_raw_html_text = st.session_state.get("html_raw_original") or ""
        render_lazy_html(original_raw_html_text, key="step1_html_raw_original", height=520, empty_message="Original HTML 산출물이 없습니다.")
    with st.expander("HTML 문서 보기", expanded=False):
        raw_html_text = st.session_state.get("html_raw") or ""
        render_lazy_html(raw_html_text, key="step1_html_raw", height=520, empty_message="HTML Raw 산출물이 없습니다.")
    with st.expander("Restored HTML 문서 보기", expanded=False):
        restored_html_text = st.session_state.get("html_raw_restored") or ""
        render_lazy_html(restored_html_text, key="step1_html_restored", height=520, empty_message="Restored HTML 산출물이 없습니다.")
    with st.expander("Markdown 문서 보기", expanded=False):
        if markdown_text:
            st.code(markdown_text, language="markdown")
        else:
            st.info("Markdown 산출물이 없습니다.")
    with st.expander("TXT 문서 보기", expanded=False):
        if txt_text:
            st.code(txt_text, language="text")
        else:
            st.info("TXT 산출물이 없습니다.")

    with st.expander("VLM 비교 모드", expanded=False):
        pdf_path_str = st.session_state.get("uploaded_pdf_path") or ""
        pdf_path = Path(pdf_path_str) if pdf_path_str else None
        base_html = st.session_state.get("html_raw_restored") or st.session_state.get("html_raw") or ""
        pymupdf_ready = has_pymupdf()
        if not pdf_path or not pdf_path.exists():
            st.info("PDF 경로가 없어 VLM 비교를 실행할 수 없습니다. 1단계를 먼저 실행해 주세요.")
        elif pdf_path.suffix.lower() != ".pdf":
            st.info("VLM 비교 모드는 PDF 업로드에서만 사용할 수 있습니다.")
        elif not base_html.strip():
            st.info("비교할 HTML Raw가 없습니다.")
        else:
            if not pymupdf_ready:
                st.warning("PyMuPDF(`fitz`)가 없어 PDF 페이지 이미지를 만들 수 없습니다. `pip install pymupdf` 후 다시 시도해 주세요.")
            st.caption("OpenDataLoader가 만든 의심 표만 골라 PDF 페이지 이미지와 함께 VLM으로 재검토합니다.")
            review_limit = st.number_input("검토할 의심 표 수", min_value=1, max_value=10, value=3, step=1, key="vlm_review_limit")
            candidate_cols = st.columns(3)
            if candidate_cols[0].button("의심 표 찾기", use_container_width=True, key="vlm_find_candidates_btn"):
                candidates = find_suspicious_table_candidates(base_html, limit=int(review_limit))
                st.session_state["vlm_table_candidates"] = [candidate.__dict__ for candidate in candidates]
                st.session_state["vlm_table_reviews"] = []
                st.session_state["vlm_table_selected_index"] = 0
                st.success(f"의심 표 {len(candidates)}건을 찾았습니다.")
            if candidate_cols[1].button(
                "의심 표 VLM 재검토",
                use_container_width=True,
                key="vlm_run_review_btn",
                disabled=not pymupdf_ready,
            ):
                stored = st.session_state.get("vlm_table_candidates") or []
                if not stored:
                    stored = [candidate.__dict__ for candidate in find_suspicious_table_candidates(base_html, limit=int(review_limit))]
                    st.session_state["vlm_table_candidates"] = stored
                reviewed_rows: list[dict] = []
                for row in stored[: int(review_limit)]:
                    candidate = TableCandidate(**row)
                    reviewed_row = candidate.__dict__.copy()
                    try:
                        reviewed_candidate, usage_info = review_candidate_with_vlm(
                            candidate,
                            model_name=st.session_state.get("llm_model", "gpt-5.5"),
                            pdf_path=pdf_path,
                        )
                        reviewed_row = reviewed_candidate.__dict__.copy()
                        reviewed_row["usage_info"] = usage_info
                    except Exception as exc:  # noqa: BLE001
                        reviewed_row["error"] = str(exc)
                    reviewed_rows.append(reviewed_row)
                st.session_state["vlm_table_reviews"] = reviewed_rows
                st.success(f"VLM 재검토 {len(reviewed_rows)}건을 완료했습니다.")
            if candidate_cols[2].button("결과 저장", use_container_width=True, key="vlm_save_reviews_btn"):
                artifact_root = current_artifact_root()
                artifact_root.mkdir(parents=True, exist_ok=True)
                candidates_path = artifact_root / "vlm_table_candidates.json"
                reviews_path = artifact_root / "vlm_table_reviews.json"
                try:
                    candidates_path.write_text(
                        json.dumps(st.session_state.get("vlm_table_candidates") or [], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    reviews_path.write_text(
                        json.dumps(st.session_state.get("vlm_table_reviews") or [], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    st.success(f"VLM 후보/결과를 저장했습니다: {artifact_root}")
                except OSError as exc:
                    st.error(f"VLM 결과 저장 실패: {exc}")

            candidates = st.session_state.get("vlm_table_candidates") or []
            reviews = st.session_state.get("vlm_table_reviews") or []
            if candidates:
                st.markdown("**의심 표 후보**")
                render_safe_table(
                    [
                        {
                            "표번호": c["index"],
                            "의심점수": round(float(c["suspicion_score"]), 2),
                            "행수": c["row_count"],
                            "열패턴": ",".join(str(v) for v in c["col_counts"]),
                            "rowspan": c["rowspan_cells"],
                            "colspan": c["colspan_cells"],
                            "페이지": c.get("page_number") or "",
                            "페이지유사도": round(float(c.get("page_match_score") or 0.0), 3),
                        }
                        for c in candidates
                    ]
                )
                selected_labels = [f"표 {c['index']} | score={float(c['suspicion_score']):.2f} | page={c.get('page_number') or '-'}" for c in candidates]
                selected_label = st.selectbox(
                    "비교할 표 선택",
                    options=selected_labels,
                    index=min(int(st.session_state.get("vlm_table_selected_index") or 0), max(len(selected_labels) - 1, 0)),
                    key="vlm_candidate_selectbox",
                )
                selected_index = selected_labels.index(selected_label) if selected_label in selected_labels else 0
                st.session_state["vlm_table_selected_index"] = selected_index
                selected_candidate = candidates[selected_index]
                selected_review = next((row for row in reviews if row.get("index") == selected_candidate["index"]), None)
                if selected_review is None:
                    st.caption("선택된 표는 아직 VLM 재검토 결과가 없습니다. 위 버튼으로 검토를 실행해 주세요.")
                else:
                    left_col, right_col = st.columns(2)
                    with left_col:
                        st.markdown("**OpenDataLoader 복원본**")
                        st.caption(
                            f"표 {selected_candidate['index']} | 의심점수 {float(selected_candidate['suspicion_score']):.2f} | "
                            f"페이지 {selected_candidate.get('page_number') or '-'} | 유사도 {float(selected_candidate.get('page_match_score') or 0.0):.3f}"
                        )
                        render_lazy_html(
                            selected_candidate["html"],
                            key=f"step1_vlm_candidate_{selected_candidate['index']}",
                            height=420,
                            empty_message="선택된 HTML이 없습니다.",
                        )
                    with right_col:
                        st.markdown("**VLM 재검토본**")
                        if selected_review.get("error"):
                            st.error(selected_review["error"])
                        else:
                            vlm_result = selected_review.get("vlm_result") or {}
                            usage_info = selected_review.get("usage_info") or {}
                            st.caption(
                                f"신뢰도 {vlm_result.get('confidence', 'medium')} | "
                                f"모델 {usage_info.get('model', st.session_state.get('llm_model', 'gpt-5.5'))}"
                            )
                            render_lazy_html(
                                vlm_result.get("table_html", ""),
                                key=f"step1_vlm_result_{selected_candidate['index']}",
                                height=420,
                                empty_message="VLM 교정 HTML이 없습니다.",
                            )
                    if selected_candidate.get("page_image_data_uri"):
                        import base64

                        image_data = selected_candidate["page_image_data_uri"].split(",", 1)[-1]
                        st.caption("페이지 이미지")
                        st.image(base64.b64decode(image_data), use_container_width=True)
                    vlm_result = selected_review.get("vlm_result") or {}
                    if vlm_result.get("notes"):
                        st.info(vlm_result["notes"])
                    if selected_review.get("usage_info"):
                        usage = selected_review["usage_info"]
                        st.caption(
                            f"비용 추정: ${float(usage.get('cost_usd', 0.0)):.6f} | "
                            f"input {usage.get('input_tokens', 0)} / output {usage.get('output_tokens', 0)} tokens"
                        )
            else:
                st.info("아직 의심 표 후보가 없습니다. '의심 표 찾기'를 눌러 주세요.")
