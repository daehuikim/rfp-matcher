#!/usr/bin/env python3
"""
data/packages 전 패키지 파이프라인 로그 생성.

출력: data/packages/logs/{package_base}/  (예: 001-하나은행, 003-신한라이프)
  - steps/00_source … 04_output
  - steps/03_extract/llm/*.prompt.txt + response.json
  - pipeline.json, README.md
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sys
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PACKAGES = ROOT / "data" / "packages"
PACKAGE_LOGS = PACKAGES / "logs"
PACKAGES_MANIFEST = PACKAGES / "packages.manifest.json"
WORK = ROOT / "temp" / "package_logs"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

from build_packages import _pipeline_input  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("package_logs")

_UUID_DIR = re.compile(r"^[0-9a-f]{16}$")


def _clean_legacy_logs() -> None:
    """content_hash prefix(16자 hex) 로 된 구 로그 폴더 제거."""
    if not PACKAGE_LOGS.is_dir():
        return
    for d in PACKAGE_LOGS.iterdir():
        if d.is_dir() and _UUID_DIR.match(d.name):
            shutil.rmtree(d)
            log.info("구 로그 삭제: %s", d.name)


def _run_one(entry: dict) -> dict:
    from prototype.v2.pipeline import run as v2_run
    from app.services.pipeline_logger import PipelineLogSession, write_step_readme

    pkg_base = entry["package_base"]
    src = Path(entry["source"])
    label = entry.get("label", pkg_base)
    out: dict = {"package_base": pkg_base, "label": label, "source": str(src)}

    if not src.is_file():
        out["error"] = f"원본 없음: {src}"
        return out

    work = WORK / pkg_base
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    log_dir = PACKAGE_LOGS / pkg_base
    if log_dir.exists():
        shutil.rmtree(log_dir)

    session = PipelineLogSession(
        PACKAGE_LOGS,
        run_id=pkg_base,
        source_path=src,
        source_name=src.name,
        engine="v2",
    )
    session.record_step(
        "meta",
        "패키지 메타",
        description=label,
        meta={"package_base": pkg_base, "label": label},
    )

    try:
        v2_input, korean_hwp = _pipeline_input(src, work)
        log.info("V2 실행: %s (%s) korean_hwp=%s", pkg_base, src.name, korean_hwp)
        manifest = v2_run(
            v2_input,
            entry.get("gold_xlsx"),
            work_root=work,
            mode="llm",
            tab_mode="ordered",
            korean_hwp=korean_hwp,
            log_session=session,
        )
        out["rows"] = manifest.get("report", {}).get("extracted_rows")
        out["pipeline_log"] = str(log_dir / "pipeline.json")
        out["llm_calls"] = len(manifest.get("llm_calls") or json.loads(
            (log_dir / "pipeline.json").read_text(encoding="utf-8")
        ).get("llm_calls", []))
        write_step_readme(log_dir)
        log.info("완료 %s — %d rows, %d LLM calls", pkg_base, out.get("rows"), out.get("llm_calls", 0))
    except Exception as e:
        out["error"] = str(e)
        log.exception("%s 실패", pkg_base)
    return out


def main() -> int:
    if not PACKAGES_MANIFEST.is_file():
        log.error("packages.manifest.json 없음 — 먼저 build_packages.py 실행")
        return 1

    meta = json.loads(PACKAGES_MANIFEST.read_text(encoding="utf-8"))
    packages = meta.get("packages") or []
    PACKAGE_LOGS.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    _clean_legacy_logs()

    results: list[dict] = []
    for entry in tqdm(packages, desc="package-logs"):
        results.append(_run_one(entry))

    summary = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "ok": sum(1 for r in results if "error" not in r),
        "failed": sum(1 for r in results if "error" in r),
        "log_root": str(PACKAGE_LOGS),
        "packages": results,
    }
    (PACKAGE_LOGS / "index.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n=== 로그 생성: {summary['ok']}/{summary['total']} ===")
    for r in results:
        if "error" not in r:
            print(f"  {r['package_base']} — {r.get('rows')} rows, LLM×{r.get('llm_calls', 0)}")
        else:
            print(f"  {r['package_base']} — ERROR: {r['error']}")
    print(f"\n출력: {PACKAGE_LOGS}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
