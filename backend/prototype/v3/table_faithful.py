"""
금융 RFP 표 충실 추출 — cell_llm 대체.

- opendataloader JSON(있으면) 또는 HTML grid → macro/fine 행 추출 (LLM 없음)
- 리스트 가./1.)/- 계위는 list_hierarchy·hierarchy_collapse 로 결정적 배치
- 탭 = 섹션 heading, 시트 순서 = 문서 페이지 등장순
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prototype.v2.blocks import dedup
from prototype.v2.document import extract_document
from prototype.v2.extract import Req, extract_grids
from prototype.v2.grid import grids_from_html, merge_consecutive_grids
from prototype.v2.hierarchy_collapse import enforce_gold_spacing, refine_hierarchy
from prototype.v2.label_carry import carry_forward_hierarchy
from prototype.v2.list_hierarchy import refine_list_hierarchy
from prototype.v2.overview import build_overview_from_html_sync, build_overview_sync
from prototype.v2.row_filter import filter_noise_rows
from prototype.v2.tab_naming import coarse_tab_from_section, sanitize_hierarchy_labels


def _ordered_tabs(reqs: list[Req]) -> list[Req]:
    for r in reqs:
        if not (r.tab or "").strip():
            r.tab = coarse_tab_from_section(r.section_path or "요구사항")
    return reqs


def postprocess_financial_reqs(reqs: list[Req], steps: list[str]) -> list[Req]:
    """v2 pipeline.py 와 동일한 결정적 후처리 — LLM 탭/계위 라벨 없음."""
    preset = [r for r in reqs if (r.tab or "").strip()]
    loose = [r for r in reqs if not (r.tab or "").strip()]
    if len(preset) >= 20:
        steps.append(f"form 기반 문서 — 자유 표 {len(loose)}행 부록 제외")
        loose = []
    if loose:
        loose.sort(key=lambda r: (r.page if r.page is not None else 9999, r.table_id))
        loose = _ordered_tabs(loose)
        steps.append("tab: 원문 섹션 순서(ordered)")
    reqs = preset + loose
    tabset = {r.tab for r in reqs}
    if len(tabset) > 1:
        reqs = [r for r in reqs if r.tab != "요구사항"]

    reqs, n_noise = filter_noise_rows(reqs)
    if n_noise:
        steps.append(f"비요구 행 제거: {n_noise}행 (현황·H/W 사양 등)")

    reqs, n_list = refine_list_hierarchy(reqs)
    if n_list:
        steps.append(f"리스트 계위(가·1)·-): {n_list}칸 재배치")

    reqs, h_steps = refine_hierarchy(reqs)
    steps.extend(h_steps)

    reqs, n_carried = carry_forward_hierarchy(reqs)
    if n_carried:
        steps.append(f"계위 carry: {n_carried}칸")

    _, n_gold = enforce_gold_spacing(reqs)
    if n_gold:
        steps.append(f"gold spacing: {n_gold}칸")

    tab_page: dict[str, int] = {}
    tab_seq: dict[str, int] = {}
    for i, r in enumerate(reqs):
        p = r.page if r.page is not None else 9999
        tab_page[r.tab] = min(tab_page.get(r.tab, 9999), p)
        tab_seq.setdefault(r.tab, i)
    reqs.sort(
        key=lambda r: (
            tab_page.get(r.tab, 9999),
            tab_seq[r.tab],
            r.page if r.page is not None else 9999,
            r.table_id,
        )
    )
    sanitize_hierarchy_labels(reqs)
    steps.append(f"tab: 페이지순 정렬 {len(tabset)}개 시트")
    return reqs


def _tab_order_page_first(reqs: list[Req]) -> list[str]:
    seen: dict[str, int] = {}
    for r in reqs:
        p = r.page if r.page is not None else 9999
        if r.tab not in seen:
            seen[r.tab] = p
        else:
            seen[r.tab] = min(seen[r.tab], p)
    return sorted(seen.keys(), key=lambda t: (seen[t], t))


def extract_table_faithful(
    *,
    doc_name: str,
    html: str,
    json_path: Path | None = None,
    extract_mode: str = "fine",
) -> tuple[list[Req], dict | None, list[str], list[str]]:
    """
    표·리스트 구조 충실 추출. 반환: (reqs, overview, steps, tab_order).
    """
    steps: list[str] = []
    overview: dict | None = None
    reqs: list[Req] = []

    if json_path and json_path.is_file():
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        reqs, _ = extract_document(doc_name, doc, extract_mode, defer_tables=False)
        reqs = dedup(reqs)
        steps.append(
            f"extract(document/{extract_mode}): JSON 1-pass 표+리스트 → {len(reqs)} rows"
        )
        overview = build_overview_sync(doc, reqs)
    else:
        grids = merge_consecutive_grids(grids_from_html(html))
        for g in grids:
            sec = g.section_heading or ""
            reqs.extend(
                extract_grids(doc_name, [g], section_heading=sec, mode=extract_mode)
            )
        reqs = dedup(reqs)
        steps.append(
            f"extract(html/{extract_mode}): 표 {len(grids)}개 → {len(reqs)} rows"
        )
        overview = build_overview_from_html_sync(html, reqs)

    if overview:
        steps.append(
            f"overview: 요약 + 기술 {len(overview.get('techs', []))} + "
            f"리스크 {len(overview.get('risks', []))}"
        )
    else:
        steps.append("overview: API 키 없음 — 개요 시트 생략")

    reqs = postprocess_financial_reqs(reqs, steps)
    tab_order = _tab_order_page_first(reqs)
    return reqs, overview, steps, tab_order
