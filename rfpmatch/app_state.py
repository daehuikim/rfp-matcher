from __future__ import annotations

import json
import os
import tempfile
from time import perf_counter
from pathlib import Path

import streamlit as st


APP_ROOT = Path(__file__).resolve().parent.parent

STEP_TITLES = {
    "step1": "1. 문서 변환",
    "step2": "2. HTML 문서 페이지 연속표 병합",
    "step3": "3. TXT 문서 목차 추출",
    "step4": "4. HTML 문서 섹션 분리 카드 생성",
    "step5": "5. 카드 단위 조견표 생성",
    "step6": "6. 부록 - 카드 분리",
}

PERSISTED_KEY_PATH = APP_ROOT / ".streamlit" / "openai_api_key.txt"
PERSISTED_COST_PATH = APP_ROOT / ".streamlit" / "llm_cost_state.json"
PERSISTED_LAST_CONVERSION_PATH = APP_ROOT / ".streamlit" / "last_conversion_bundle.json"
PROFILE_EVENT_LIMIT = 50


def default_progress() -> dict:
    return {
        "step1": {"state": "pending", "message": "PDF/DOCX/HWP/HWPX 업로드 후 HTML 변환 대기"},
        "step2": {"state": "pending", "message": "HTML 변환 후 연속표 병합 가능"},
        "step3": {"state": "pending", "message": "HTML 변환 후 목차 추출 가능"},
        "step4": {"state": "pending", "message": "최종 목차 저장 후 카드 생성 가능"},
        "step5": {"state": "pending", "message": "카드 생성 후 카드 단위 조견표 생성 가능"},
        "step6": {"state": "pending", "message": "카드 생성 후 카드 분리 가능"},
    }


def mark_running(step_key: str, message: str) -> None:
    st.session_state["progress"][step_key] = {"state": "running", "message": message}


def mark_done(step_key: str, message: str) -> None:
    st.session_state["progress"][step_key] = {"state": "done", "message": message}
    st.session_state["current_step"] = step_key


def mark_error(step_key: str, message: str) -> None:
    st.session_state["progress"][step_key] = {"state": "error", "message": message}


def load_openai_api_key() -> str:
    env_key = os.getenv("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key
    if PERSISTED_KEY_PATH.exists():
        return PERSISTED_KEY_PATH.read_text(encoding="utf-8").strip()
    return ""


def persist_openai_api_key(api_key: str) -> None:
    value = api_key.strip()
    if not value:
        return
    PERSISTED_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PERSISTED_KEY_PATH.write_text(value, encoding="utf-8")
    os.environ["OPENAI_API_KEY"] = value


def sync_openai_api_key_input() -> None:
    value = st.session_state.get("openai_api_key_input", "").strip()
    if value:
        persist_openai_api_key(value)


def clear_persisted_openai_api_key() -> None:
    if PERSISTED_KEY_PATH.exists():
        PERSISTED_KEY_PATH.unlink()
    os.environ.pop("OPENAI_API_KEY", None)


def load_persisted_llm_cost_state() -> tuple[float, list[dict]]:
    if not PERSISTED_COST_PATH.exists():
        return 0.0, []
    try:
        payload = json.loads(PERSISTED_COST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0.0, []
    total = float(payload.get("total_usd", 0.0) or 0.0)
    logs = payload.get("logs", [])
    if not isinstance(logs, list):
        logs = []
    return total, logs


def persist_llm_cost_state(total_usd: float, logs: list[dict]) -> None:
    PERSISTED_COST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"total_usd": float(total_usd), "logs": logs}
    PERSISTED_COST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def persist_last_conversion_bundle_path(bundle_path: Path) -> None:
    PERSISTED_LAST_CONVERSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    PERSISTED_LAST_CONVERSION_PATH.write_text(
        json.dumps({"bundle_path": str(bundle_path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_last_conversion_bundle_path() -> Path | None:
    if not PERSISTED_LAST_CONVERSION_PATH.exists():
        return None
    try:
        payload = json.loads(PERSISTED_LAST_CONVERSION_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    bundle_path = payload.get("bundle_path")
    if not bundle_path:
        return None
    path = Path(bundle_path)
    if not path.exists():
        return None
    return path


def record_profile_event(name: str, elapsed_ms: float, *, detail: str = "") -> None:
    events = list(st.session_state.get("profile_events") or [])
    events.append({
        "name": name,
        "elapsed_ms": round(float(elapsed_ms), 2),
        "detail": detail,
    })
    st.session_state["profile_events"] = events[-PROFILE_EVENT_LIMIT:]


def profile_block(name: str, *, detail: str = ""):
    class _ProfileBlock:
        def __enter__(self):
            self._started = perf_counter()
            return self

        def __exit__(self, exc_type, exc, tb):
            record_profile_event(name, (perf_counter() - self._started) * 1000.0, detail=detail)
            return False

    return _ProfileBlock()


def ensure_state() -> None:
    if "progress" not in st.session_state:
        st.session_state["progress"] = default_progress()
    if "current_step" not in st.session_state:
        st.session_state["current_step"] = "step1"
    if "workdir" not in st.session_state:
        st.session_state["workdir"] = tempfile.mkdtemp(prefix="rfpmatch_")
    for key, default in {
        "file_digest": None,
        "file_name": None,
        "html": None,
        "basic_html": None,
        "html_raw": None,
        "html_raw_original": None,
        "html_raw_restored": None,
        "html_raw_postprocessed": None,
        "html_raw_empty_column_postprocessed": None,
        "html_raw_merged_empty_up_postprocessed": None,
        "empty_column_postprocessed": False,
        "html_merged": None,
        "html_merged_raw": None,
        "html_merged_from_raw": None,
        "html_merged_from_raw_postprocessed": None,
        "html_merged_from_postprocessed": None,
        "html_merged_source": None,
        "html_merged_summary_from_raw": None,
        "html_merged_summary_from_raw_postprocessed": None,
        "html_merged_summary_from_postprocessed": None,
        "vlm_table_candidates": None,
        "vlm_table_reviews": None,
        "vlm_table_selected_index": 0,
        "outputs": None,
        "page_summary": None,
        "toc_area_items": None,
        "toc_body_items": None,
        "toc_items": None,
        "saved_toc_items": None,
        "cards_step2": None,
        "cards_step2_signature": None,
        "cards_step2_split": None,
        "cards_step2_split_selected_ids": [],
        "cards_step3": None,
        "cards_step4": None,
        "cards_step5": None,
        "llm_schema_rows": None,
        "llm_deep_schema_rows": None,
        "markdown_llm_toc_items": None,
        "txt_llm_toc_items": None,
        "html_merged_summary": None,
        "step2_merged_html": None,
        "step2_toc_html": None,
        "step2_body_html": None,
        "uploaded_pdf_name": None,
        "uploaded_pdf_bytes": None,
        "uploaded_pdf_path": None,
        "view_step_pending": None,
        "step5_section_requirement_running": False,
        "step5_section_requirement_status": "",
        "step5_section_requirement_autostart": False,
        "step4_use_merged_html": True,
        "step4_html_source_mode": "merged",
        "step4_use_merged_html_widget": True,
        "step4_generated_html_source": "",
        "profile_events": [],
    }.items():
        if key not in st.session_state:
            st.session_state[key] = default
    stored_api_key = load_openai_api_key()
    if "openai_api_key_input" not in st.session_state:
        st.session_state["openai_api_key_input"] = stored_api_key
    elif not str(st.session_state.get("openai_api_key_input") or "").strip() and stored_api_key:
        st.session_state["openai_api_key_input"] = stored_api_key
    if "llm_model" not in st.session_state:
        st.session_state["llm_model"] = "gpt-4o"
    if "llm_cost_total_usd" not in st.session_state or "llm_cost_logs" not in st.session_state:
        total, logs = load_persisted_llm_cost_state()
        st.session_state["llm_cost_total_usd"] = total
        st.session_state["llm_cost_logs"] = logs
    if "llm_cost_last_msg" not in st.session_state:
        st.session_state["llm_cost_last_msg"] = ""
    if "step5_use_llm" not in st.session_state:
        st.session_state["step5_use_llm"] = False
    if "step4_use_merged_html" not in st.session_state:
        st.session_state["step4_use_merged_html"] = True
    if "step4_html_source_mode" not in st.session_state:
        st.session_state["step4_html_source_mode"] = "merged"
    if "step4_use_merged_html_widget" not in st.session_state:
        st.session_state["step4_use_merged_html_widget"] = st.session_state["step4_html_source_mode"] == "merged"
    if "toc_editor_version" not in st.session_state:
        st.session_state["toc_editor_version"] = 0
    if "step2_active_tab" not in st.session_state:
        st.session_state["step2_active_tab"] = "목차 추출"
    if "autoloader_tried" not in st.session_state:
        st.session_state["autoloader_tried"] = False
    if "html_merged_summary" not in st.session_state:
        st.session_state["html_merged_summary"] = None


def reset_pipeline(file_name: str | None = None, file_digest: str | None = None) -> None:
    st.session_state["progress"] = default_progress()
    st.session_state["current_step"] = "step1"
    st.session_state["view_step_pending"] = "step1"
    st.session_state["file_name"] = file_name
    st.session_state["file_digest"] = file_digest
    for key, default in {
        "html": None,
        "basic_html": None,
        "html_raw": None,
        "html_raw_original": None,
        "html_raw_restored": None,
        "html_raw_postprocessed": None,
        "html_raw_empty_column_postprocessed": None,
        "html_raw_merged_empty_up_postprocessed": None,
        "empty_column_postprocessed": False,
        "html_merged": None,
        "html_merged_raw": None,
        "html_merged_from_raw": None,
        "html_merged_from_raw_postprocessed": None,
        "html_merged_from_postprocessed": None,
        "html_merged_source": None,
        "html_merged_summary_from_raw": None,
        "html_merged_summary_from_raw_postprocessed": None,
        "html_merged_summary_from_postprocessed": None,
        "vlm_table_candidates": None,
        "vlm_table_reviews": None,
        "vlm_table_selected_index": 0,
        "outputs": None,
        "page_summary": None,
        "toc_area_items": None,
        "toc_body_items": None,
        "toc_items": None,
        "saved_toc_items": None,
        "cards_step2": None,
        "cards_step2_signature": None,
        "cards_step2_split": None,
        "cards_step2_split_selected_ids": [],
        "cards_step3": None,
        "cards_step4": None,
        "cards_step5": None,
        "llm_schema_rows": None,
        "llm_deep_schema_rows": None,
        "markdown_llm_toc_items": None,
        "txt_llm_toc_items": None,
        "html_merged_summary": None,
        "step2_merged_html": None,
        "step2_toc_html": None,
        "step2_body_html": None,
        "uploaded_pdf_name": None,
        "uploaded_pdf_bytes": None,
        "uploaded_pdf_path": None,
        "step5_section_requirement_running": False,
        "step5_section_requirement_status": "",
        "step5_section_requirement_autostart": False,
        "step4_use_merged_html": True,
        "step4_html_source_mode": "merged",
        "step4_use_merged_html_widget": True,
        "step4_generated_html_source": "",
        "profile_events": [],
    }.items():
        st.session_state[key] = default
    st.session_state["toc_editor_version"] = 0
    st.session_state["step2_active_tab"] = "목차 추출"
    st.session_state["autoloader_tried"] = False
