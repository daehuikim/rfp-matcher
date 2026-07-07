"""rfpmatch 파이프라인 오케스트레이터 — 문서 → 요구사항표. rfpmatch/step1~6 자동 조립.

원본 Streamlit 마법사(step1 변환 → step2 표 병합 → step3 TOC 확정 → step4 섹션/카드 →
step5 요구사항표 → step6 카드 분할)의 단계별 "사람이 확인 후 다음" 지점을 전부 자동 실행으로
대체한다. 문서 변환은 prototype.v_rule.convert.convert_any 재사용(OpenDataLoader + 스캔 PDF
Gemma VLM OCR + 앱 HWP/HWPX/DOCX 변환기 — rfpmatch 자체 변환기는 포팅하지 않음).

VLM 표 재구성(vlm_review.py의 find_suspicious_table_candidates/review_candidate_with_vlm)은
원본에서도 사람에게 보여주는 진단용일 뿐 base html을 실제로 고쳐쓰지 않았으므로(step1_convert.py
확인 — "적용" 액션 없음), 여기서도 파이프라인 산출물에 영향을 주지 않는 자동 교정으로
연결하지 않는다. 필요해지면 별도로 연결한다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from prototype.v_rule.convert import convert_any

from .cards import build_rfp_cards
from .html_tables import merge_consecutive_tables_in_html_raw, merge_empty_cells_upward_in_html
from .models import RfpCard, TocItem
from .requirement_table import build_section_requirement_tables
from .sections import build_sections_from_final_toc
from .toc import extract_toc, extract_toc_views
from .toc_llm import build_llm_toc_from_raw_document
from .toc_normalize import (
    drop_toc_heading_items,
    fill_missing_pages_from_neighbors,
    reconcile_pages_from_candidates,
    relevel_items,
    trim_toc_depth,
)


def _merge_and_postprocess_tables(html: str) -> str:
    """step2: 페이지에 걸쳐 잘린 연속 표 병합 + 위칸으로 빈 셀 채움."""
    merged = merge_consecutive_tables_in_html_raw(html)
    return merge_empty_cells_upward_in_html(merged)


def _build_final_toc(
    html: str,
    raw_text: str,
    *,
    source_name: str,
    client: object | None,
) -> list[TocItem]:
    """step3: LLM 목차 생성 + 자동 복구(페이지 보정/레벨 재조정/목차머리말 제거/깊이 제한).

    LLM 실패(키 없음/네트워크 등) 시 예외를 전파하지 않고 빈 리스트를 반환 — 호출부가
    규칙 기반 extract_toc(html)로 폴백한다.
    """
    try:
        area_items, body_items, _merged_items = extract_toc_views(html)
        html_page_candidates = area_items + body_items
        toc_items = build_llm_toc_from_raw_document(raw_text, source_name, client=client)
        toc_items = relevel_items(toc_items)
        toc_items = drop_toc_heading_items(toc_items)
        toc_items = reconcile_pages_from_candidates(toc_items, html_page_candidates)
        return trim_toc_depth(fill_missing_pages_from_neighbors(toc_items))
    except Exception:
        return []


def run(
    doc_path: str | Path,
    workdir: str | Path,
    *,
    client: object | None = None,
    use_llm: bool = True,
    disable_dedup: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """문서 → rfpmatch 파이프라인 전체 실행.

    반환: {"section_tables", "debug_rows", "cards", "sections", "toc_items", "match_debug"}.
    변환 실패(html 산출물 없음)면 전부 빈 값으로 채운 동일 구조를 반환한다.
    """

    def _progress(text: str) -> None:
        if on_progress is not None:
            on_progress(text)

    src = Path(doc_path)
    work = Path(workdir)
    empty_result = {
        "section_tables": {},
        "debug_rows": [],
        "cards": [],
        "sections": [],
        "toc_items": [],
        "match_debug": [],
    }

    _progress(f"문서 변환 중: {src.name}")
    conv = convert_any(src, work)
    if "html" not in conv:
        return empty_result
    html = conv["html"].read_text(encoding="utf-8", errors="replace")
    raw_text = conv["txt"].read_text(encoding="utf-8", errors="replace") if "txt" in conv else ""

    _progress("연속 표 병합 중")
    html = _merge_and_postprocess_tables(html)

    toc_items: list[TocItem] = []
    if use_llm and raw_text.strip():
        _progress("목차 추출 중 (LLM 자동복구 포함)")
        toc_items = _build_final_toc(html, raw_text, source_name=src.stem, client=client)
    if not toc_items:
        _progress("목차 추출 중 (규칙 기반 폴백)")
        toc_items = extract_toc(html)

    _progress("섹션/카드 구성 중")
    sections, match_debug = build_sections_from_final_toc(html, toc_items)
    cards: list[RfpCard] = build_rfp_cards(sections)

    _progress("요구사항표 생성 중")
    section_tables, debug_rows = build_section_requirement_tables(
        cards,
        client=client,
        use_llm=use_llm,
        disable_dedup=disable_dedup,
        on_progress=on_progress,
    )
    _progress("파이프라인 완료")
    return {
        "section_tables": section_tables,
        "debug_rows": debug_rows,
        "cards": cards,
        "sections": sections,
        "toc_items": toc_items,
        "match_debug": match_debug,
    }
