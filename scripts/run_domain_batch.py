#!/usr/bin/env python3
"""도메인 coarsening 배치 — 신한·하나·법제처·금감원 V2 파이프라인 검증."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import openpyxl
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed" / "domain_test"
WORK = ROOT / "temp" / "domain_batch"

sys.path.insert(0, str(BACKEND))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("domain_batch")

SAMPLES = [
    {
        "label": "신한",
        "path": RAW / "(신한라이프) AX HUB 구축_제안요청서.pdf.pdf",
        "kind": "pdf",
    },
    {
        "label": "하나",
        "path": RAW / "하나.pdf",
        "kind": "pdf",
    },
    {
        "label": "법제처",
        "path": RAW / "법제처_생성형 AI 법령검색 시스템 구축_RFI_20260116.html",
        "kind": "html_korean",
    },
    {
        "label": "금감원_24년",
        "path": RAW / "금감원제안요청서_24년.hwp",
        "kind": "hwp",
    },
]


def _hwp_to_html(hwp: Path, out_dir: Path) -> Path:
    hwp5html = shutil.which("hwp5html")
    if not hwp5html:
        raise RuntimeError("hwp5html 없음")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    proc = subprocess.run([hwp5html, "--output", str(out_dir), str(hwp)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[:500])
    src = out_dir / "index.xhtml"
    dest = out_dir / f"{hwp.stem}.html"
    shutil.copyfile(src, dest)
    return dest


def _analyze_xlsx(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True)
    stats = {"tabs": {}, "duplicate_ids": 0, "total_rows": 0}
    all_ids: Counter[str] = Counter()
    for ws in wb.worksheets:
        if ws.title in ("개요",):
            continue
        rows = []
        tops = anchors = 0
        prefixes: Counter[str] = Counter()
        for r in ws.iter_rows(min_row=2, values_only=True):
            if not r or len(r) < 5 or not r[4]:
                continue
            rid = str(r[0] or "")
            top = str(r[1] or "") if r[1] else ""
            if rid:
                all_ids[rid] += 1
                if "_" in rid:
                    prefixes[rid.rsplit("_", 1)[0]] += 1
            if top:
                anchors += 1
            rows.append(r)
        stats["tabs"][ws.title] = {
            "rows": len(rows),
            "top_anchors": anchors,
            "prefixes": len(prefixes),
            "top_prefixes": prefixes.most_common(6),
        }
        stats["total_rows"] += len(rows)
    wb.close()
    stats["duplicate_ids"] = sum(1 for c in all_ids.values() if c > 1)
    stats["unique_ids"] = len(all_ids)
    return stats


def _run_one(sample: dict) -> dict:
    from prototype.v2.pipeline import run as v2_run

    label = sample["label"]
    src = Path(sample["path"])
    if not src.is_file():
        raise FileNotFoundError(src)

    input_path = src
    korean_hwp = False
    if sample["kind"] == "hwp":
        input_path = _hwp_to_html(src, WORK / label)
        korean_hwp = True
    elif sample["kind"] == "html_korean":
        korean_hwp = True

    log.info("파이프라인 시작: %s", label)
    manifest = v2_run(str(input_path), None, mode="llm", tab_mode="ordered", korean_hwp=korean_hwp)
    xlsx = Path(manifest["artifacts"]["requirements_xlsx"])
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{label}.xlsx"
    shutil.copyfile(xlsx, dest)
    stats = _analyze_xlsx(dest)
    report = {
        "label": label,
        "source": str(src),
        "rows": manifest["report"]["extracted_rows"],
        "steps": manifest["steps"],
        "excel": str(dest),
        "stats": stats,
    }
    (WORK / f"{label}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "%s: %d rows, %d dup IDs, tabs=%d → %s",
        label,
        stats["total_rows"],
        stats["duplicate_ids"],
        len(stats["tabs"]),
        dest,
    )
    return report


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    reports = []
    for s in tqdm(SAMPLES, desc="samples"):
        try:
            reports.append(_run_one(s))
        except Exception as e:
            log.error("%s 실패: %s", s["label"], e)
            reports.append({"label": s["label"], "error": str(e)})

    summary = WORK / "summary.json"
    summary.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== domain coarsening 배치 ===")
    for r in reports:
        if "error" in r:
            print(f"  {r['label']}: ERROR — {r['error']}")
            continue
        st = r["stats"]
        proj = next((v for k, v in st["tabs"].items() if "프로젝트" in k or "업무" in k), None)
        extra = ""
        if proj:
            extra = f" anchors={proj['top_anchors']} prefixes={proj['prefixes']}"
        print(f"  {r['label']}: {st['total_rows']} rows, dupIDs={st['duplicate_ids']}{extra}")
    print(f"\n출력: {OUT}\n요약: {summary}")
    return 0 if all("error" not in r for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
