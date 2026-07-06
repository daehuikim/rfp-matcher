from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from time import perf_counter

import streamlit as st

from rfpmatch.app_state import persist_last_conversion_bundle_path, record_profile_event


def load_text_file(path_str: str | None, max_chars: int | None = None) -> str:
    if not path_str:
        return ""
    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n...[truncated]..."
    return text


def format_bytes(num_bytes: int) -> str:
    value = float(max(num_bytes, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}GB"


def load_bundle_text(payload: dict, outputs: dict, field: str, *fallback_fields: str) -> str:
    candidate_fields = (field, *fallback_fields)
    for candidate in candidate_fields:
        value = payload.get(candidate)
        if isinstance(value, str) and value.strip():
            return value
        text = load_text_file(outputs.get(candidate))
        if text.strip():
            return text
    return ""


def render_lazy_html(
    html_text: str | None,
    *,
    key: str,
    height: int = 520,
    empty_message: str = "표시할 HTML이 없습니다.",
) -> bool:
    text = html_text or ""
    if not text.strip():
        st.info(empty_message)
        return False
    size_label = format_bytes(len(text.encode("utf-8")))
    st.caption(f"HTML 크기: {size_label}")
    started = perf_counter()
    st.components.v1.html(text, height=height, scrolling=True)
    record_profile_event(f"html_view:{key}", (perf_counter() - started) * 1000.0, detail=size_label)
    return True


def render_safe_table(
    data,
    *,
    editable: bool = False,
    key: str | None = None,
    num_rows: str | int | None = None,
    use_container_width: bool = True,
    hide_index: bool = True,
):
    rows = data if isinstance(data, list) else [data]

    if editable:
        if not rows:
            st.caption("표 데이터가 없습니다.")
            return []
        st.caption("표를 직접 편집할 수 있습니다. 셀을 수정한 뒤 다음 단계로 진행하세요.")
        edited = st.data_editor(
            rows,
            use_container_width=use_container_width,
            hide_index=hide_index,
            num_rows=num_rows or "dynamic",
            key=key,
        )
        return edited

    if not rows:
        st.caption("표 데이터가 없습니다.")
        return rows

    columns: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            for col in row.keys():
                if col not in columns:
                    columns.append(str(col))

    if not columns:
        st.json(rows)
        return rows

    def _cell_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    header_html = "".join(f"<th>{col}</th>" for col in columns)
    body_rows = []
    for row in rows:
        if not isinstance(row, dict):
            body_rows.append(f"<tr><td colspan=\"{len(columns)}\">{_cell_text(row)}</td></tr>")
            continue
        cells = "".join(f"<td>{_cell_text(row.get(col, ''))}</td>" for col in columns)
        body_rows.append(f"<tr>{cells}</tr>")
    table_html = (
        "<table style='width:100%; border-collapse:collapse; font-size:0.92rem;'>"
        "<thead><tr>"
        f"{header_html}"
        "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
    )
    st.markdown(table_html, unsafe_allow_html=True)
    return rows


def rows_to_xlsx_bytes(sheet_map: dict[str, list[dict]]) -> bytes:
    def col_name(idx: int) -> str:
        name = ""
        while idx:
            idx, rem = divmod(idx - 1, 26)
            name = chr(65 + rem) + name
        return name

    def xml_escape(value: object) -> str:
        text = "" if value is None else str(value)
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    worksheet_files: dict[str, str] = {}
    workbook_sheets: list[str] = []
    rels_entries: list[str] = []
    for idx, (sheet_name, rows) in enumerate(sheet_map.items(), start=1):
        safe_sheet_name = sheet_name[:31] or f"Sheet{idx}"
        workbook_sheets.append(f'<sheet name="{xml_escape(safe_sheet_name)}" sheetId="{idx}" r:id="rId{idx}"/>')
        rels_entries.append(
            f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        )
        headers = list(rows[0].keys()) if rows else []
        all_rows = [headers] + [[row.get(h, "") for h in headers] for row in rows]
        xml_rows = []
        for r_idx, row in enumerate(all_rows, start=1):
            cells = []
            for c_idx, value in enumerate(row, start=1):
                ref = f"{col_name(c_idx)}{r_idx}"
                style = ' s="1"' if r_idx == 1 else ""
                cells.append(
                    f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{xml_escape(value)}</t></is></c>'
                )
            xml_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
        worksheet_files[f"xl/worksheets/sheet{idx}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheetData>" + "".join(xml_rows) + "</sheetData></worksheet>"
        )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(sheet_map) + 1)
            )
            + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>'
        ))
        zf.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'
        ))
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels_entries)
            + '<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>'
        ))
        for path, xml in worksheet_files.items():
            zf.writestr(path, xml)
        zf.writestr("xl/styles.xml", styles_xml)
    return buffer.getvalue()


def current_artifact_root() -> Path:
    outputs = st.session_state.get("outputs") or {}
    for key in ("html_raw", "html_raw_postprocessed", "html_raw_empty_column_postprocessed", "markdown", "txt", "html"):
        raw_path = outputs.get(key)
        if not raw_path:
            continue
        path = Path(raw_path)
        parent = path.parent
        if parent.name == "artifacts":
            return parent / Path(st.session_state.get("file_name") or "document").stem
        if parent.parent.name == "artifacts":
            return parent
    return Path("artifacts") / Path(st.session_state.get("file_name") or "document").stem


def save_step1_bundle(*, include_merge_outputs: bool = False) -> Path | None:
    file_name = st.session_state.get("file_name")
    html = st.session_state.get("html")
    if not file_name or not html:
        return None
    artifact_root = current_artifact_root()
    artifact_root.mkdir(parents=True, exist_ok=True)
    outputs = st.session_state.get("outputs") or {}
    persisted_outputs: dict[str, str | None] = {}
    for key in ("html", "html_raw", "html_raw_original", "html_raw_postprocessed", "markdown", "json", "txt"):
        src = outputs.get(key)
        if not src:
            persisted_outputs[key] = None
            continue
        src_path = Path(src)
        if not src_path.exists():
            persisted_outputs[key] = None
            continue
        ext = src_path.suffix or f".{key}"
        dst = artifact_root / f"{key}{ext}"
        try:
            dst.write_bytes(src_path.read_bytes())
            persisted_outputs[key] = str(dst.resolve())
        except OSError:
            persisted_outputs[key] = None
    if not persisted_outputs.get("html"):
        html_path = artifact_root / "document.html"
        html_path.write_text(html, encoding="utf-8")
        persisted_outputs["html"] = str(html_path.resolve())
    if st.session_state.get("html_raw") and not persisted_outputs.get("html_raw"):
        raw_html_path = artifact_root / "document_html_raw.html"
        raw_html_path.write_text(st.session_state["html_raw"], encoding="utf-8")
        persisted_outputs["html_raw"] = str(raw_html_path.resolve())
    if st.session_state.get("html_raw_original") and not persisted_outputs.get("html_raw_original"):
        original_raw_html_path = artifact_root / "document_html_raw_original.html"
        original_raw_html_path.write_text(st.session_state["html_raw_original"], encoding="utf-8")
        persisted_outputs["html_raw_original"] = str(original_raw_html_path.resolve())
    if st.session_state.get("html_raw_empty_column_postprocessed") and not persisted_outputs.get("html_raw_empty_column_postprocessed"):
        postprocessed_raw_html_path = artifact_root / "document_html_raw_빈칸제거_postprocessed.html"
        postprocessed_raw_html_path.write_text(st.session_state["html_raw_empty_column_postprocessed"], encoding="utf-8")
        persisted_outputs["html_raw_empty_column_postprocessed"] = str(postprocessed_raw_html_path.resolve())
    if include_merge_outputs:
        for key, filename in {
            "html_merged_from_raw": "document_merged_tables_from_raw.html",
            "html_raw_merged_empty_up_postprocessed": "document_html_raw_단순병합_빈칸윗칸병합_postprocessed.html",
            "html_merged_from_raw_postprocessed": "document_html_raw_단순병합_빈칸윗칸병합_postprocessed.html",
            "html_merged_from_postprocessed": "document_merged_tables_from_postprocessed.html",
        }.items():
            value = st.session_state.get(key)
            if value and not persisted_outputs.get(key):
                path = artifact_root / filename
                path.write_text(value, encoding="utf-8")
                persisted_outputs[key] = str(path.resolve())
    bundle = {
        "bundle_version": 2,
        "file_name": file_name,
        "file_digest": st.session_state.get("file_digest"),
        "step4_use_merged_html": bool(st.session_state.get("step4_use_merged_html", True)),
        "step4_html_source_mode": str(st.session_state.get("step4_html_source_mode") or ("merged" if st.session_state.get("step4_use_merged_html", True) else "raw")),
        "html": None,
        "html_raw": None,
        "html_raw_original": None,
        "html_raw_postprocessed": None,
        "html_raw_empty_column_postprocessed": None,
        "html_raw_merged_empty_up_postprocessed": None,
        "empty_column_postprocessed": st.session_state.get("empty_column_postprocessed", False),
        "html_merged_raw": None,
        "html_merged_from_raw": None,
        "html_merged_from_raw_postprocessed": None,
        "html_merged_from_postprocessed": None,
        "html_merged_source": st.session_state.get("html_merged_source"),
        "html_merged_summary_from_raw": st.session_state.get("html_merged_summary_from_raw"),
        "html_merged_summary_from_raw_postprocessed": st.session_state.get("html_merged_summary_from_raw_postprocessed"),
        "html_merged_summary_from_postprocessed": st.session_state.get("html_merged_summary_from_postprocessed"),
        "outputs": persisted_outputs,
        "page_summary": st.session_state.get("page_summary"),
    }
    bundle_path = artifact_root / "step1_bundle.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    persist_last_conversion_bundle_path(bundle_path.resolve())
    return bundle_path


def restore_step1_bundle(bundle_path: Path) -> bool:
    try:
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    outputs = payload.get("outputs") or {}
    html = load_bundle_text(payload, outputs, "html", "basic_html")
    if not html.strip():
        return False
    st.session_state["file_name"] = payload.get("file_name")
    st.session_state["file_digest"] = payload.get("file_digest")
    restored_mode = str(payload.get("step4_html_source_mode") or "").strip().lower()
    if restored_mode not in {"merged", "raw"}:
        restored_mode = "merged" if bool(payload.get("step4_use_merged_html", True)) else "raw"
    st.session_state["step4_html_source_mode"] = restored_mode
    st.session_state["step4_use_merged_html"] = restored_mode == "merged"
    st.session_state["step4_use_merged_html_widget"] = restored_mode == "merged"
    st.session_state["html"] = html
    outputs.pop("html_merged_raw", None)
    st.session_state["html_raw"] = load_bundle_text(payload, outputs, "html_raw")
    st.session_state["html_raw_original"] = load_bundle_text(payload, outputs, "html_raw_original", "html_raw")
    st.session_state["html_raw_restored"] = load_bundle_text(payload, outputs, "html_raw_restored", "html_raw") or st.session_state["html_raw"]
    st.session_state["html_raw_postprocessed"] = load_bundle_text(payload, outputs, "html_raw_postprocessed", "html_raw_restored") or st.session_state["html_raw_restored"]
    st.session_state["html_raw_empty_column_postprocessed"] = load_bundle_text(payload, outputs, "html_raw_empty_column_postprocessed")
    st.session_state["html_raw_merged_empty_up_postprocessed"] = load_bundle_text(payload, outputs, "html_raw_merged_empty_up_postprocessed")
    st.session_state["empty_column_postprocessed"] = bool(payload.get("empty_column_postprocessed", False))
    st.session_state["html_merged"] = None
    st.session_state["html_merged_raw"] = load_bundle_text(payload, outputs, "html_merged_raw")
    st.session_state["html_merged_from_raw"] = load_bundle_text(payload, outputs, "html_merged_from_raw")
    st.session_state["html_merged_from_raw_postprocessed"] = load_bundle_text(payload, outputs, "html_merged_from_raw_postprocessed")
    st.session_state["html_merged_from_postprocessed"] = load_bundle_text(payload, outputs, "html_merged_from_postprocessed")
    st.session_state["html_merged_from_raw_postprocessed"] = st.session_state["html_raw_merged_empty_up_postprocessed"] or st.session_state["html_merged_from_raw_postprocessed"]
    st.session_state["html_merged_source"] = payload.get("html_merged_source")
    st.session_state["html_merged_summary_from_raw"] = payload.get("html_merged_summary_from_raw")
    st.session_state["html_merged_summary_from_raw_postprocessed"] = payload.get("html_merged_summary_from_raw_postprocessed")
    st.session_state["html_merged_summary_from_postprocessed"] = payload.get("html_merged_summary_from_postprocessed")
    outputs.pop("html_merged_raw", None)
    outputs.pop("html_merged", None)
    st.session_state["outputs"] = outputs
    st.session_state["page_summary"] = payload.get("page_summary")
    st.session_state["toc_area_items"] = None
    st.session_state["toc_body_items"] = None
    st.session_state["toc_items"] = None
    st.session_state["saved_toc_items"] = None
    st.session_state["cards_step2"] = None
    st.session_state["cards_step2_split"] = None
    st.session_state["cards_step2_split_selected_ids"] = []
    st.session_state["cards_step3"] = None
    st.session_state["cards_step4"] = None
    st.session_state["cards_step5"] = None
    st.session_state["llm_schema_rows"] = None
    st.session_state["llm_deep_schema_rows"] = None
    st.session_state["html_merged_summary"] = None
    st.session_state["step2_merged_html"] = None
    st.session_state["step2_toc_html"] = None
    st.session_state["step2_body_html"] = None
    st.session_state["progress"]["step1"] = {"state": "done", "message": f"저장본 로드 완료: {bundle_path}"}
    st.session_state["progress"]["step2"] = {"state": "pending", "message": "목차 추출 후 실행 가능"}
    st.session_state["progress"]["step3"] = {"state": "pending", "message": "HTML 변환 후 실행 가능"}
    return True
