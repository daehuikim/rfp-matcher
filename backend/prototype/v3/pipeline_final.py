"""
Final 4-sample pipeline.

- public_form: PDF→HTML → korean_form (총괄표·form 블록 룰베이스)
- table_faithful: PDF→HTML/JSON → 표·리스트 구조 충실 추출 (금융 RFP)
- cell_llm: (deprecated) table_faithful 로 위임
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from prototype.v2.excel_writer import write_excel
from prototype.v3.financial_excel_writer import write_financial_excel
from prototype.v2.grid import read_html_bytes
from prototype.v2.ids import assign_ids
from prototype.v2.image_placeholders import attach_figure_images, attach_screen_images
from prototype.v2.korean_form import extract_korean_form_document, public_form_overview
from prototype.v2.table_images import attach_pipe_table_images
from prototype.v2.overview import build_overview_from_html_sync
from prototype.v2.tab_naming import sanitize_hierarchy_labels
from prototype.v2.validate import completeness, load_gold

from .cell_chunker import chunk_html
from .cell_llm import units_to_reqs_sync
from .convert import native_to_html, resolve_public_form_source
from .financial_rfp import extract_financial_rfp

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _find_cached_html(workdir: Path) -> Path | None:
    """work/ 에 이미 변환된 HTML 이 있으면 경로 반환."""
    if not workdir.is_dir():
        return None
    htmls = sorted(workdir.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in htmls:
        if p.name.endswith(".raw.html"):
            continue
        if p.stat().st_size > 500:
            return p
    return None


def run_sample(
    *,
    sample_id: str,
    source: Path,
    strategy: str,
    label: str,
    out_root: Path,
    gold_xlsx: Path | None = None,
    log_session=None,
    extract_only: bool = False,
    llm_concurrency: int | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    workdir = out_root / sample_id / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    steps: list[str] = []
    name = source.stem

    if log_session is not None:
        log_session.record_source()

    convert_src = source
    if strategy == "public_form":
        alt = resolve_public_form_source(source, sample_id)
        if alt != source:
            convert_src = alt
            steps.append(f"convert: HWPX 우선 — {alt.name} (PDF 페이지 절단 회피)")
    cached = _find_cached_html(workdir) if extract_only else None
    json_path: Path | None = None
    raw_path: Path | None = None
    if cached is not None:
        html_path = cached
        json_candidates = sorted(workdir.glob("*.json"), key=lambda p: p.stat().st_size, reverse=True)
        json_path = json_candidates[0] if json_candidates else None
        raw_sib = cached.with_name(cached.stem + ".raw.html")
        raw_path = raw_sib if raw_sib.is_file() else None
        steps.append(f"convert: skip (cached {html_path.name}, {html_path.stat().st_size} bytes)")
        converter = "cached"
    else:
        html_path, raw_path, json_path, converter = native_to_html(convert_src, workdir)
        steps.append(f"convert: {converter} → html ({html_path.stat().st_size} bytes)")

    if log_session is not None:
        log_session.record_convert_raw(
            converter=converter,
            raw_html=raw_path or html_path,
            raw_json=json_path,
            input_ref=convert_src,
        )
        log_session.record_convert_postprocessed(
            post_html=html_path,
            raw_html=raw_path,
            converter=converter,
        )

    html = html_path.read_text(encoding="utf-8", errors="replace")
    overview = None
    tab_order: list[str] | None = None
    reqs = []

    if strategy == "public_form":
        korean = extract_korean_form_document(html, name)
        if korean is None:
            steps.append("public_form: 총괄표 미탐지 — cell_llm 폴백")
            report = chunk_html(html)
            steps.append(
                f"chunk: units={len(report.units)} nested_table={report.nested_tables} "
                f"images={report.images} bullets={report.bullets}"
            )
            _save_chunk_report(out_root / sample_id, report)
            reqs = units_to_reqs_sync(report.units, doc_name=name)
        else:
            reqs = korean.reqs
            meta = build_overview_from_html_sync(html, reqs)
            if meta:
                steps.append("overview: LLM 개요·핵심기술·리스크 생성")
            elif meta is None:
                steps.append("overview: API 키 없음 — 총괄표만 출력")
            overview = (
                public_form_overview(korean.summary, meta)
                if korean.summary
                else None
            )
            steps.extend(korean.steps)
            # nested/image 탐지 리포트
            report = chunk_html(html)
            steps.append(
                f"nested/image 탐지: nested_table={report.nested_tables} images={report.images}"
            )
            _save_chunk_report(out_root / sample_id, report)
    elif strategy in ("table_faithful", "cell_llm"):
        reqs, overview, tf_steps, tab_order = extract_financial_rfp(
            doc_name=name,
            html=html,
            json_path=json_path,
            llm_concurrency=llm_concurrency,
        )
        steps.extend(tf_steps)
        report = chunk_html(html)
        steps.append(
            f"nested/image 탐지: nested_table={report.nested_tables} images={report.images}"
        )
        _save_chunk_report(out_root / sample_id, report)
    else:
        report = chunk_html(html)
        steps.append(
            f"chunk: units={len(report.units)} nested_table={report.nested_tables} "
            f"images={report.images} bullets={report.bullets}"
        )
        _save_chunk_report(out_root / sample_id, report)
        reqs = units_to_reqs_sync(report.units, doc_name=name)
        steps.append(f"cell_llm: {len(reqs)} requirements from {len(report.units)} units")
        tab_order = None

    sanitize_hierarchy_labels(reqs)
    assign_ids(reqs)

    table_cache = out_root / sample_id / "work" / "table_images"
    if strategy == "public_form":
        reqs, n_tbl = attach_pipe_table_images(reqs, table_cache)
        if n_tbl:
            steps.append(f"표안표 PNG 렌더: {n_tbl}건")
        if raw_path and raw_path.is_file():
            reqs, n_img = attach_screen_images(reqs, raw_path)
            if n_img:
                steps.append(f"관련 화면(안) 이미지 매칭: {n_img}건")
            reqs, n_fig = attach_figure_images(reqs, raw_path)
            if n_fig:
                steps.append(f"화면 breadcrumb 이미지 매칭: {n_fig}건")

    xlsx_path = out_root / sample_id / "requirements.xlsx"
    if strategy in ("table_faithful", "cell_llm"):
        write_financial_excel(reqs, xlsx_path, overview=overview, tab_order=tab_order)
    else:
        write_excel(reqs, xlsx_path, overview=overview, tab_order=tab_order)
    steps.append(f"excel: {len(reqs)} rows → {xlsx_path.name}")

    report: dict[str, Any] = {"extracted_rows": len(reqs), "strategy": strategy, "label": label}
    if gold_xlsx and gold_xlsx.is_file():
        gold = load_gold(str(gold_xlsx))
        res = completeness(gold, reqs)
        report.update(
            {
                "gold_total": res["gold_total"],
                "covered": res["covered"],
                "recall": round(res["recall"], 4),
                "missing": len(res["missing"]),
                "missing_by_sheet": res["missing_by_sheet"],
                "gold_xlsx": str(gold_xlsx),
            }
        )
        steps.append(f"gold recall: {res['recall']*100:.1f}% ({res['covered']}/{res['gold_total']})")

    if log_session is not None:
        log_session.record_extract(mode=strategy, pipeline_steps=steps, row_count=len(reqs))
        log_session.record_output(xlsx_path=xlsx_path, report=report)
        log_session.finalize(extra={"strategy": strategy, "sample_id": sample_id})
        from app.services.pipeline_logger import write_step_readme

        write_step_readme(log_session.log_dir)

    manifest = {
        "sample_id": sample_id,
        "source": str(source),
        "strategy": strategy,
        "label": label,
        "steps": steps,
        "report": report,
        "artifacts": {
            "html_postprocessed": str(html_path),
            "html_raw": str(raw_path) if raw_path else None,
            "requirements_xlsx": str(xlsx_path),
        },
        "_reqs": reqs,
        "_overview": overview,
    }
    (out_root / sample_id / "manifest.json").write_text(
        json.dumps(
            {k: v for k, v in manifest.items() if not k.startswith("_")},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _save_v3_export(out_root / sample_id, reqs=reqs, overview=overview, strategy=strategy)
    return manifest


def _save_v3_export(sample_dir: Path, *, reqs, overview, strategy: str) -> None:
    import pickle

    sample_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "reqs": reqs,
        "overview": overview,
        "strategy": strategy,
        "sample_id": sample_dir.name,
    }
    (sample_dir / "v3_export.pkl").write_bytes(pickle.dumps(payload))


def _save_chunk_report(sample_dir: Path, report) -> None:
    sample_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "unit_count": len(report.units),
        "nested_tables": report.nested_tables,
        "images": report.images,
        "bullets": report.bullets,
        "units_sample": [
            {"uid": u.uid, "kind": u.kind, "text": u.text[:200], "section": u.section[:60]}
            for u in report.units[:50]
        ],
    }
    (sample_dir / "chunk_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
