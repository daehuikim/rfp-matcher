"""
오케스트레이터 — 원본(비정형) → 변환 → 추출 → 엑셀 + 정합성 리포트.

모든 중간 산출물을 data/v2_work/<문서>/ 에 영속 저장해 추적 가능하게 한다.
  source.json / source.html  : 변환 결과(중간 산출물)
  requirements.xlsx          : 추출된 요구사항 표
  report.json                : recall·누락(정답 엑셀 주어진 경우)
  manifest.json              : 모든 경로·카운트·파이프라인 단계 기록

입력 타입:
  .pdf  → opendataloader 변환(JSON+HTML) → 표 + 리스트/문단 추출
  .html → 인코딩 감지 후 파싱 → 표 추출
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from .blocks import dedup
from .document import extract_document
from .excel_writer import write_excel
from .extract import Req, extract_grids
from .grid import grids_from_html, read_html_bytes
from .ids import assign_ids
from .llm_atomize import atomize_reqs_sync
from .llm_cell_atomize import atomize_cells_sync
from .consistency import validate_reqs
from .label_carry import carry_forward_hierarchy
from .hierarchy_collapse import enforce_gold_spacing, refine_hierarchy
from .list_hierarchy import refine_list_hierarchy
from .llm_meta import assign_hierarchy_labels_sync
from .row_filter import filter_noise_rows
from .tab_naming import coarse_tab_from_section, sanitize_hierarchy_labels
from .llm_schema import schema_extract_tables_sync
from .llm_tabvalidate import validate_tabs_sync
from .text import norm
from .overview import build_overview_sync
from .validate import completeness, load_gold

REPO_ROOT = Path(__file__).resolve().parents[3]
WORK_ROOT = REPO_ROOT / "data" / "v2_work"
_OPENJDK = "/opt/homebrew/opt/openjdk@17/bin"


def _ensure_java() -> None:
    # macOS 의 /usr/bin/java 는 비작동 스텁일 수 있으므로, 실제 openjdk 가 있으면
    # PATH 앞에 붙여 우선하게 한다. (JAVA_HOME 도 함께 설정)
    if Path(_OPENJDK, "java").exists():
        os.environ["PATH"] = f"{_OPENJDK}:{os.environ.get('PATH', '')}"
        home = Path(_OPENJDK).parent / "libexec" / "openjdk.jdk" / "Contents" / "Home"
        if home.exists():
            os.environ["JAVA_HOME"] = str(home)


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]


def _ordered_tabs(reqs: list[Req]) -> list[Req]:
    """문서 섹션 heading 단위 탭 — 정보보호 4.x 는 통합, ICT 2.4/2.5 는 분리."""
    for r in reqs:
        r.tab = coarse_tab_from_section(r.section_path or "요구사항")
    return reqs


def run(input_path: str | Path, gold_xlsx: str | None = None,
        work_root: Path = WORK_ROOT, mode: str = "fine",
        tab_mode: str = "cluster", korean_hwp: bool = False,
        log_session=None, post_process: bool = True) -> dict:
    src = Path(input_path)
    name = src.stem
    workdir = work_root / _slug(name)
    workdir.mkdir(parents=True, exist_ok=True)
    steps: list[str] = []
    artifacts: dict[str, str] = {}
    overview: dict | None = None
    sheet_tab_order: list[str] | None = None
    convert_converter = ""

    from app.services.pipeline_logger import set_active_session

    if log_session is not None:
        log_session.record_source()
        set_active_session(log_session)

    # llm 모드 = macro 추출 후 LLM 원자화 패스
    extract_mode = "macro" if mode == "llm" else mode
    overview_src = None  # 개요용 원문(PDF JSON 또는 HTML 텍스트)

    ext = src.suffix.lower()
    from_html = ext in (".html", ".htm")
    if ext == ".pdf":
        _ensure_java()
        import opendataloader_pdf
        # 이전 실행 잔여물 제거(오선택 방지)
        for stale in list(workdir.glob("*.json")) + list(workdir.glob("*.html")):
            stale.unlink()
        convert_converter = "opendataloader-pdf"
        opendataloader_pdf.convert(input_path=[str(src)], output_dir=str(workdir),
                                   format="json,html")
        steps.append("convert: opendataloader-pdf → json,html")
        _RESERVED = {"manifest.json", "report.json"}
        json_path = next((p for p in workdir.glob("*.json")
                          if p.name not in _RESERVED), None)
        if json_path is None:
            raise RuntimeError("opendataloader produced no JSON")
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        overview_src = doc
        artifacts["source_json"] = str(json_path)
        html_path = next((p for p in workdir.glob("*.html")), None)
        if html_path:
            if log_session is not None:
                log_session.record_convert_raw(
                    converter=convert_converter,
                    raw_html=html_path,
                    raw_json=json_path,
                    input_ref=src if src.is_file() else None,
                )
            from app.phase1.converters.html_postprocess import postprocess_html_file, raw_html_sibling

            tables, paragraphs = postprocess_html_file(html_path)
            steps.append(f"postprocess: compact_html → tables={tables} paragraphs={paragraphs}")
            artifacts["source_html"] = str(html_path)
            raw_backup = raw_html_sibling(html_path)
            if raw_backup.is_file():
                artifacts["source_html_raw"] = str(raw_backup)
            if log_session is not None:
                log_session.record_convert_postprocessed(
                    post_html=html_path,
                    raw_html=raw_backup if raw_backup.is_file() else None,
                    converter=convert_converter,
                )
        if mode == "llm":
            # LLM 스키마설계 + 결정적 실행: 표는 LLM 이 스키마만 짜고 executor 가 내용 이동(누락·할루시0)
            list_reqs, cands = extract_document(name, doc, "fine", defer_tables=True)
            table_reqs = schema_extract_tables_sync(cands)
            for r in table_reqs:
                r.doc = name
            # 평문 헤더+불릿이 grid 행 단위로 흩어진 표(messy)만 LLM few-shot 재구성
            before_n = len(table_reqs)
            table_reqs = atomize_cells_sync(table_reqs)
            if len(table_reqs) != before_n:
                steps.append(f"셀 원자화: 헤더-불릿 재구성 {before_n}→{len(table_reqs)} rows")
            reqs = dedup(list_reqs + table_reqs)
            steps.append(f"extract(llm-schema): 표 {len(cands)}개 스키마설계+결정적실행 + 리스트 → {len(reqs)} rows")
        else:
            reqs, _ = extract_document(name, doc, extract_mode)
            reqs = dedup(reqs)
            steps.append(f"extract({extract_mode}): 문서순서 1-pass(표+리스트, 계위 매핑) → {len(reqs)} rows")
    elif ext in (".html", ".htm"):
        from app.phase1.converters.html_postprocess import postprocess_html_file, raw_html_sibling

        html = read_html_bytes(src.read_bytes())
        from bs4 import BeautifulSoup
        overview_src = BeautifulSoup(html, "lxml").get_text(" ")
        out_html = workdir / "source.html"

        raw_html_path = src.with_suffix(".raw.html")
        if raw_html_path.is_file():
            convert_converter = "app-converter+postprocess"
            post_html_path = src
            steps.append("convert: app-converter html (already postprocessed)")
        else:
            convert_converter = "html-direct"
            out_html.write_text(html, encoding="utf-8")
            post_html_path = out_html
            steps.append("convert: html (encoding-detected) → utf-8 copy")
            if log_session is not None:
                log_session.record_convert_raw(
                    converter=convert_converter,
                    raw_html=post_html_path,
                    input_ref=src,
                )
            postprocess_html_file(post_html_path)
            raw_html_path = raw_html_sibling(post_html_path)
            steps.append("postprocess: compact_html (html-direct)")

        artifacts["source_html"] = str(post_html_path)
        if raw_html_path.is_file():
            artifacts["source_html_raw"] = str(raw_html_path)
        if log_session is not None:
            if convert_converter == "app-converter+postprocess":
                log_session.record_convert_raw(
                    converter=convert_converter,
                    raw_html=raw_html_path,
                    input_ref=src,
                )
            log_session.record_convert_postprocessed(
                post_html=post_html_path,
                raw_html=raw_html_path if raw_html_path.is_file() else None,
                converter=convert_converter,
            )

        # HWP/HWPX 공공 RFP — 요구사항 총괄표·form 블록 전용 경로 (PDF 등은 기존 로직)
        korean_handled = False
        if korean_hwp:
            from .korean_form import extract_korean_form_document, public_form_overview
            from .overview import build_overview_from_html_sync

            korean = extract_korean_form_document(html, name)
            if korean is not None:
                reqs = korean.reqs
                meta = build_overview_from_html_sync(html, reqs)
                if meta:
                    steps.append(
                        f"개요 생성: 요약 + 기술 {len(meta['techs'])} + 리스크 {len(meta['risks'])}"
                    )
                overview = (
                    public_form_overview(korean.summary, meta)
                    if korean.summary
                    else None
                )
                if korean.summary:
                    sheet_tab_order = korean.summary.tab_order
                steps.extend(korean.steps)
                korean_handled = True

        if not korean_handled:
            grids = grids_from_html(html)
        if not korean_handled and mode == "llm":
            from .classify import classify, detect_header_row, is_requirement_table

            cands: list[tuple] = []
            for g in grids:
                hr = detect_header_row(g)
                roles = classify(g, hr)
                if is_requirement_table(g, roles, hr):
                    sec = g.section_heading or ""
                    cands.append((g, sec))
            table_reqs = schema_extract_tables_sync(cands)
            for r in table_reqs:
                r.doc = name
                if not r.section_path:
                    for g, sec in cands:
                        if g.table_id == r.table_id and sec:
                            r.section_path = norm(sec)
                            break
            before_n = len(table_reqs)
            table_reqs = atomize_cells_sync(table_reqs)
            if len(table_reqs) != before_n:
                steps.append(
                    f"셀 원자화(html): {before_n}→{len(table_reqs)} rows"
                )
            reqs = dedup(table_reqs)
            steps.append(
                f"extract(llm-schema/html): 표 {len(cands)}개 스키마설계 → {len(reqs)} rows"
            )
        elif not korean_handled:
            reqs = []
            for g in grids:
                reqs.extend(
                    extract_grids(
                        name, [g], section_heading=g.section_heading, mode=extract_mode
                    )
                )
            reqs = dedup(reqs)
            steps.append(f"extract({extract_mode}): tables({len(grids)}) → {len(reqs)} rows")
    else:
        raise ValueError(f"지원하지 않는 입력 타입: {ext}")

    # 동적 경로(run_dynamic): 후처리(탭 배정/노이즈 드롭/계위/정렬/ids/excel)는 finalize 가 담당.
    # 여기선 **추출 원본 reqs**(드롭 없음 — 100% recall) + 개요만 만들어 반환.
    if not post_process:
        if mode == "llm" and overview_src is not None:
            overview = build_overview_sync(overview_src, reqs)
            if overview:
                steps.append(
                    f"개요 생성: 요약 + 기술 {len(overview['techs'])} + 리스크 {len(overview['risks'])}"
                )
        from .async_run import reset_loop

        m = {
            "source": str(src), "name": name, "input_type": ext, "steps": steps,
            "artifacts": artifacts, "report": {"extracted_rows": len(reqs)},
            "_reqs": reqs, "_overview": overview, "_workdir": str(workdir),
        }
        try:
            reset_loop()
        except RuntimeError:
            pass
        return m

    # 한글 form 경로는 탭·부록이 이미 배정됨 — LLM 탭/메타 단계 생략
    korean_form_done = korean_hwp and any(
        s.startswith("한글 form:") or s.startswith("form 추출:") for s in steps
    )

    if korean_form_done:
        steps.append(f"tab: 총괄표 기준 {len({r.tab for r in reqs})} 탭")
    else:
        # 사전 배정된 행(세로형 표의 SFR/DAR 등)은 그대로 두고, 미배정 행만 그룹핑
        preset = [r for r in reqs if r.tab]
        loose = [r for r in reqs if not r.tab]
        # 세로형 폼 표가 요구사항 본문을 담은 문서(법제처 등)는 자유 표를 부록으로 간주 → 제외
        if len(preset) >= 20:
            steps.append(f"form 기반 문서 — 자유 표 {len(loose)}행 부록 제외")
            loose = []
        if loose:
            loose.sort(key=lambda r: (r.page if r.page is not None else 9999, r.table_id))
            loose = _ordered_tabs(loose)
            steps.append("tab: 원문 섹션 순서(ordered)")
        reqs = preset + loose
        # 구체 탭이 여럿이면 generic 'fallback' 탭(요구사항)은 비요구 잔여로 보고 제거
        tabset = {r.tab for r in reqs}
        if len(tabset) > 1:
            reqs = [r for r in reqs if r.tab != "요구사항"]
        steps.append(f"tab: 사전 {len({r.tab for r in preset})} + 그룹핑 {len({r.tab for r in loose})} 탭")

    # 탭 검수 — 비요구(참조안내/범위설명/현황/절차) 탭 제거.
    # 폼 탭도 비요구일 수 있으므로 모두 검수 대상(가장 큰 탭·drop_cap 안전장치로 보호).
    if mode == "llm" and not korean_form_done:
        before_tabs = len({r.tab for r in reqs})
        reqs = validate_tabs_sync(reqs)
        after_tabs = len({r.tab for r in reqs})
        if after_tabs < before_tabs:
            steps.append(f"탭 검수: 비요구 탭 {before_tabs - after_tabs}개 제거")

    if not korean_form_done:
        reqs, n_noise = filter_noise_rows(reqs)
        if n_noise:
            steps.append(f"비요구 행 제거: {n_noise}행 (현황·H/W 사양 등)")

        reqs, n_list = refine_list_hierarchy(reqs)
        if n_list:
            steps.append(f"리스트 계위(가·1)·-): {n_list}칸 재배치")

        reqs, h_steps = refine_hierarchy(reqs)
        steps.extend(h_steps)

        if mode != "llm":
            reqs, n_carried = carry_forward_hierarchy(reqs)
            if n_carried:
                steps.append(f"계위 carry(bullet 제외): {n_carried}칸")

    # LLM 이 항목명·요구사항을 정답 조견표 형식(연속행 빈칸)으로 생성
    if mode == "llm" and not korean_form_done:
        before_gen = sum(1 for r in reqs if r.gen_top or r.gen_mid)
        reqs = assign_hierarchy_labels_sync(reqs)
        after_gen = sum(1 for r in reqs if r.gen_top or r.gen_mid)
        steps.append(f"LLM 계위 라벨: 신규 gen {after_gen - before_gen}칸")
        _, n_gold = enforce_gold_spacing(reqs)
        if n_gold:
            steps.append(f"gold spacing(LLM 후): {n_gold}칸")

    consistency = validate_reqs(reqs)
    if not consistency.ok:
        steps.append(f"정합성 검토: {consistency.summary()}")

    # 탭을 raw 문서 첫 등장 페이지순으로 정렬(사람 방식). 페이지 없으면 등장순 보존.
    tab_page: dict[str, int] = {}
    tab_seq: dict[str, int] = {}
    for i, r in enumerate(reqs):
        p = r.page if r.page is not None else 9999
        tab_page[r.tab] = min(tab_page.get(r.tab, 9999), p)
        tab_seq.setdefault(r.tab, i)
    if korean_form_done and sheet_tab_order:
        rank = {t: i for i, t in enumerate(sheet_tab_order)}
        reqs.sort(
            key=lambda r: (
                rank.get(r.tab, 999 if r.tab != "부록" else 1000),
                r.rid or "",
            )
        )
    else:
        reqs.sort(
            key=lambda r: (
                tab_page.get(r.tab, 9999),
                tab_seq[r.tab],
                r.page if r.page is not None else 9999,
            )
        )

    sanitize_hierarchy_labels(reqs)

    assign_ids(reqs)

    if overview is None and mode == "llm" and overview_src is not None and not korean_form_done:
        overview = build_overview_sync(overview_src, reqs)
        if overview:
            steps.append(f"개요 생성: 요약 + 기술 {len(overview['techs'])} + 리스크 {len(overview['risks'])}")

    xlsx_path = workdir / "requirements.xlsx"
    write_excel(reqs, xlsx_path, overview=overview, tab_order=sheet_tab_order)
    artifacts["requirements_xlsx"] = str(xlsx_path)

    report: dict = {"extracted_rows": len(reqs)}
    if gold_xlsx:
        gold = load_gold(gold_xlsx)
        res = completeness(gold, reqs)
        report.update({
            "gold_total": res["gold_total"],
            "covered": res["covered"],
            "recall": round(res["recall"], 4),
            "missing": len(res["missing"]),
            "missing_by_sheet": res["missing_by_sheet"],
            "missing_samples": [{"sheet": m.sheet, "text": m.text[:120]}
                                for m in res["missing"][:30]],
            "gold_xlsx": gold_xlsx,
        })
        (workdir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["report_json"] = str(workdir / "report.json")

    if log_session is not None:
        log_session.record_extract(
            mode=mode,
            pipeline_steps=steps,
            row_count=len(reqs),
            meta={"tab_mode": tab_mode, "korean_hwp": korean_hwp},
        )
        log_session.record_output(xlsx_path=xlsx_path, report=report)
        log_session.finalize(
            extra={
                "v2_workdir": str(workdir),
                "input_type": ext,
                "artifacts": artifacts,
            }
        )
        from app.services.pipeline_logger import write_step_readme

        write_step_readme(log_session.log_dir)
        set_active_session(None)

    manifest = {
        "source": str(src),
        "name": name,
        "input_type": ext,
        "steps": steps,
        "artifacts": artifacts,
        "report": report,
    }
    if log_session is not None:
        manifest["pipeline_log"] = str(log_session.log_dir / "pipeline.json")
    (workdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["manifest_json"] = str(workdir / "manifest.json")
    # 앱 통합용 — 파일에는 안 쓰고 객체만 반환에 첨부 (JSON 직렬화 후라 안전)
    manifest["_reqs"] = reqs
    manifest["_overview"] = overview
    manifest["_workdir"] = str(workdir)

    from .async_run import reset_loop

    try:
        reset_loop()
    except RuntimeError:
        pass
    return manifest


def _main() -> None:
    """usage: python -m prototype.v2.pipeline <src> [gold.xlsx] [mode] [tab_mode]
       mode: fine | llm(기본 권장)   tab_mode: cluster(tech-aware) | ordered(PDF순서)"""
    import sys
    src = sys.argv[1]
    gold = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].endswith(".xlsx") else None
    rest = [a for a in sys.argv[2:] if not a.endswith(".xlsx")]
    mode = rest[0] if len(rest) > 0 else "llm"
    tab_mode = rest[1] if len(rest) > 1 else "cluster"
    m = run(src, gold, mode=mode, tab_mode=tab_mode)
    print(f"=== {m['name']} ({m['input_type']}) ===")
    for s in m["steps"]:
        print(" ·", s)
    r = m["report"]
    if "recall" in r:
        print(f"recall {r['recall']*100:.1f}%  ({r['covered']}/{r['gold_total']}, "
              f"누락 {r['missing']})  누락시트={r['missing_by_sheet']}")
    print("산출물:")
    for k, v in m["artifacts"].items():
        print(f"   {k}: {v}")


if __name__ == "__main__":
    _main()
