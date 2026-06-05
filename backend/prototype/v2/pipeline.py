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
import re
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
from .llm_cluster import cluster_tabs_sync
from .llm_meta import generate_metadata_sync
from .llm_schema import schema_extract_tables_sync
from .llm_tabvalidate import validate_tabs_sync
from .overview import build_overview_sync
from .llm_tabs import assign_tabs_sync
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


# 번호/로마숫자 접두는 뒤에 구분자(. ) 또는 공백)가 있어야 함 — 'ICT' 의 'IC' 오인 방지
# ASCII 로마숫자·한글번호는 점/괄호 필수(LLM·ICT 등 영어약어 보호). 숫자·전각로마는 공백도 허용.
_TAB_NUM = re.compile(
    r"^\s*(?:[IVXLCDM]+[.)]|[가나다라마바사아자차카타파하][.)]|[Ⅰ-Ⅻ]\.?|\d+(?:\.\d+)*\.?)\s+")
_TAB_BULLET = re.compile(r"^\s*[❍○●▪∙•◦·\-–—]\s*")
_TAB_BRACKET = re.compile(r"\[[^\]]*\]|\([^)]*\)|「[^」]*」")


def _clean_tab_name(section_path: str) -> str:
    seg = section_path.split(" > ")[-1]
    seg = _TAB_BRACKET.sub("", seg)
    # 목차(TOC) 점선 leader + 페이지번호 제거: "과제 제안요청서 ········· 7" → "과제 제안요청서"
    seg = re.sub(r"[·.·…‧\.\-_\s]{3,}\d*\s*$", "", seg)
    seg = re.sub(r"\s+\d+\s*$", "", seg)
    for _ in range(3):
        new = _TAB_BULLET.sub("", _TAB_NUM.sub("", seg))
        if new == seg:
            break
        seg = new
    seg = seg.strip()
    return seg[:40] or (section_path.split(" > ")[-1][:40] or "요구사항")


def _ordered_tabs(reqs: list[Req]) -> list[Req]:
    """PDF 순서 탭 — 문서 섹션 heading 단위. 같은 섹션은(비연속이라도) 한 탭으로 묶고,
    다른 섹션은 별도 탭. 탭 순서/탭내 행은 이후 페이지순 정렬이 처리(전역 클러스터링 X).
    """
    for r in reqs:
        r.tab = _clean_tab_name(r.section_path or "요구사항")
    return reqs


def run(input_path: str | Path, gold_xlsx: str | None = None,
        work_root: Path = WORK_ROOT, mode: str = "fine",
        tab_mode: str = "cluster") -> dict:
    src = Path(input_path)
    name = src.stem
    workdir = work_root / _slug(name)
    workdir.mkdir(parents=True, exist_ok=True)
    steps: list[str] = []
    artifacts: dict[str, str] = {}

    # llm 모드 = macro 추출 후 LLM 원자화 패스
    extract_mode = "macro" if mode == "llm" else mode
    overview_src = None  # 개요용 원문(PDF JSON 또는 HTML 텍스트)

    ext = src.suffix.lower()
    if ext == ".pdf":
        _ensure_java()
        import opendataloader_pdf
        # 이전 실행 잔여물 제거(오선택 방지)
        for stale in list(workdir.glob("*.json")) + list(workdir.glob("*.html")):
            stale.unlink()
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
            artifacts["source_html"] = str(html_path)
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
        html = read_html_bytes(src.read_bytes())
        from bs4 import BeautifulSoup
        overview_src = BeautifulSoup(html, "lxml").get_text(" ")
        out_html = workdir / "source.html"
        out_html.write_text(html, encoding="utf-8")
        artifacts["source_html"] = str(out_html)
        steps.append("convert: html (encoding-detected) → utf-8 copy")
        grids = grids_from_html(html)
        reqs = dedup(extract_grids(name, grids, mode=extract_mode))
        steps.append(f"extract({extract_mode}): tables({len(grids)}) → {len(reqs)} rows")
    else:
        raise ValueError(f"지원하지 않는 입력 타입: {ext}")

    # 사전 배정된 행(세로형 표의 SFR/DAR 등)은 그대로 두고, 미배정 행만 그룹핑
    preset = [r for r in reqs if r.tab]
    loose = [r for r in reqs if not r.tab]
    # 세로형 폼 표가 요구사항 본문을 담은 문서(법제처 등)는 자유 표를 부록으로 간주 → 제외
    if len(preset) >= 20:
        steps.append(f"form 기반 문서 — 자유 표 {len(loose)}행 부록 제외")
        loose = []
    if loose:
        if mode == "llm" and tab_mode == "ordered":
            # PDF 순서 탭(표/리스트 candidate 순서, 전역 클러스터링 X)
            loose.sort(key=lambda r: (r.page if r.page is not None else 9999, r.table_id))
            loose = _ordered_tabs(loose)
        elif mode == "llm":
            # 전역 content 기반 클러스터링 — LLM 이 전체를 보고 탭 체계 설계+배정
            loose = cluster_tabs_sync(loose)
        else:
            if not any(r.section_path for r in loose):
                sample: dict[int, str] = {}
                for r in loose:
                    sample.setdefault(r.table_id, r.detail[:60])
                for r in loose:
                    r.section_path = f"표 내용: {sample[r.table_id]}"
            loose = assign_tabs_sync(loose)
            loose = [r for r in loose if r.tab and r.tab != "제외"]
    reqs = preset + loose
    # 구체 탭이 여럿이면 generic 'fallback' 탭(요구사항)은 비요구 잔여로 보고 제거
    tabset = {r.tab for r in reqs}
    if len(tabset) > 1:
        reqs = [r for r in reqs if r.tab != "요구사항"]
    steps.append(f"tab: 사전 {len({r.tab for r in preset})} + 그룹핑 {len({r.tab for r in loose})} 탭")

    # 탭 검수 — 비요구(참조안내/범위설명/현황/절차) 탭 제거.
    # 폼 탭도 비요구일 수 있으므로 모두 검수 대상(가장 큰 탭·drop_cap 안전장치로 보호).
    if mode == "llm":
        before_tabs = len({r.tab for r in reqs})
        reqs = validate_tabs_sync(reqs)
        after_tabs = len({r.tab for r in reqs})
        if after_tabs < before_tabs:
            steps.append(f"탭 검수: 비요구 탭 {before_tabs - after_tabs}개 제거")

    # 결정적 carry-forward 후에도 빈 계위(주로 리스트 콘텐츠)는 LLM 이 라벨 생성(gen 플래그→색구분)
    if mode == "llm":
        empties = sum(1 for r in reqs if not r.top.strip() or not r.mid.strip())
        if empties:
            reqs = generate_metadata_sync(reqs)
            steps.append(f"빈 계위 LLM 생성: {empties}건 (셀 색 구분)")

    # 탭을 raw 문서 첫 등장 페이지순으로 정렬(사람 방식). 페이지 없으면 등장순 보존.
    tab_page: dict[str, int] = {}
    tab_order: dict[str, int] = {}
    for i, r in enumerate(reqs):
        p = r.page if r.page is not None else 9999
        tab_page[r.tab] = min(tab_page.get(r.tab, 9999), p)
        tab_order.setdefault(r.tab, i)
    reqs.sort(key=lambda r: (tab_page.get(r.tab, 9999), tab_order[r.tab],
                             r.page if r.page is not None else 9999))

    assign_ids(reqs)

    overview = None
    if mode == "llm" and overview_src is not None:
        overview = build_overview_sync(overview_src, reqs)
        if overview:
            steps.append(f"개요 생성: 요약 + 기술 {len(overview['techs'])} + 리스크 {len(overview['risks'])}")

    xlsx_path = workdir / "requirements.xlsx"
    write_excel(reqs, xlsx_path, overview=overview)
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

    manifest = {
        "source": str(src),
        "name": name,
        "input_type": ext,
        "steps": steps,
        "artifacts": artifacts,
        "report": report,
    }
    (workdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["manifest_json"] = str(workdir / "manifest.json")
    # 앱 통합용 — 파일에는 안 쓰고 객체만 반환에 첨부 (JSON 직렬화 후라 안전)
    manifest["_reqs"] = reqs
    manifest["_overview"] = overview
    manifest["_workdir"] = str(workdir)
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
