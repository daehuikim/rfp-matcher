#!/usr/bin/env python3
"""
final_raw 4종 → artifacts_final 배치 실험.

  data/artifacts_final/{sample_id}/
    manifest.json
    requirements.xlsx
    chunk_report.json
    logs/  (pipeline.json, steps/, llm/)
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FINAL_MANIFEST = ROOT / "data" / "final.manifest.json"
RAW = ROOT / "data" / "raw"

sys.path.insert(0, str(BACKEND))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("final_batch")


def _resolve_path(p: Path) -> Path | None:
    if p.is_file():
        return p
    parent, name = p.parent, p.name
    if not parent.is_dir():
        return None
    import unicodedata

    target = unicodedata.normalize("NFC", name)
    for f in parent.iterdir():
        if unicodedata.normalize("NFC", f.name) == target:
            return f
    return None


def _sync_raw(samples: list[dict]) -> None:
    """final_raw → data/raw (프론트 샘플 API용)."""
    RAW.mkdir(parents=True, exist_ok=True)
    for s in samples:
        src = _resolve_path(ROOT / s["source"])
        name = s.get("sample_name") or (src.name if src else "")
        dest = RAW / name
        if src and src.is_file():
            shutil.copy2(src, dest)
            log.info("raw 동기화: %s", name)


DOMAIN_TEST_DIR = ROOT / "data" / "processed" / "domain_test"
DOMAIN_TEST_NAMES: dict[str, str] = {
    "001-하나은행": "하나.xlsx",
    "002-신한라이프": "신한.xlsx",
    "003-법제처": "법제처.xlsx",
    "004-금감원": "금감원_24년.xlsx",
}


def publish_domain_test(out_root: Path, sample_ids: list[str] | None = None) -> list[str]:
    """artifacts_final → data/processed/domain_test (확인용 xlsx 복사)."""
    DOMAIN_TEST_DIR.mkdir(parents=True, exist_ok=True)
    published: list[str] = []
    for sid, fname in DOMAIN_TEST_NAMES.items():
        if sample_ids and sid not in sample_ids:
            continue
        src = out_root / sid / "requirements.xlsx"
        if not src.is_file():
            log.warning("domain_test 스킵 (없음): %s", src)
            continue
        dest = DOMAIN_TEST_DIR / fname
        shutil.copy2(src, dest)
        published.append(str(dest))
        log.info("domain_test: %s → %s", sid, dest.name)
    return published


def _run_one(
    entry: dict,
    out_root: Path,
    *,
    extract_only: bool = False,
    llm_concurrency: int = 16,
) -> dict:
    import os

    os.environ["LLM_CONCURRENCY"] = str(llm_concurrency)
    from app.services.pipeline_logger import PipelineLogSession, set_active_session
    from prototype.v3.pipeline_final import run_sample

    sample_id = entry["id"]
    source = _resolve_path(ROOT / entry["source"])
    gold = _resolve_path(ROOT / entry["gold_xlsx"]) if entry.get("gold_xlsx") else None
    out: dict = {"id": sample_id, "label": entry.get("label", "")}

    if source is None or not source.is_file():
        out["error"] = f"원본 없음: {entry['source']}"
        return out

    if (out_root / sample_id / "logs").exists():
        shutil.rmtree(out_root / sample_id / "logs")

    session = PipelineLogSession(
        out_root / sample_id,
        run_id="logs",
        source_path=source,
        source_name=source.name,
        engine="v3-final",
    )
    set_active_session(session)

    try:
        manifest = run_sample(
            sample_id=sample_id,
            source=source,
            strategy=entry["strategy"],
            label=entry.get("label", sample_id),
            out_root=out_root,
            gold_xlsx=gold,
            log_session=session,
            extract_only=extract_only,
            llm_concurrency=llm_concurrency,
        )
        out["report"] = manifest.get("report", {})
        out["steps"] = manifest.get("steps", [])[-3:]
        log.info(
            "%s 완료 — rows=%s recall=%s",
            sample_id,
            out["report"].get("extracted_rows"),
            out["report"].get("recall"),
        )
    except Exception as e:
        out["error"] = str(e)
        log.exception("%s 실패", sample_id)
    finally:
        set_active_session(None)

    return out


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="final_raw 4종 → artifacts_final 배치")
    parser.add_argument(
        "--only",
        help="쉼표 구분 sample id (예: 003-법제처,004-금감원)",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="work/ HTML 캐시 사용 — PDF 변환 생략",
    )
    parser.add_argument("--workers", type=int, default=1, help="샘플 병렬 수")
    parser.add_argument("--llm-concurrency", type=int, default=16, help="LLM 동시 호출")
    args = parser.parse_args()

    meta = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    out_root = ROOT / meta.get("artifacts_root", "data/artifacts_final")
    samples = meta["samples"]
    if args.only:
        allow = {s.strip() for s in args.only.split(",") if s.strip()}
        samples = [s for s in samples if s["id"] in allow]
        if not samples:
            log.error("--only 에 해당하는 샘플 없음: %s", args.only)
            return 1
    out_root.mkdir(parents=True, exist_ok=True)

    _sync_raw(samples)

    est_per = 180 if args.extract_only else 360
    est_total = est_per * len(samples) // max(1, args.workers)
    eta = datetime.now() + __import__("datetime").timedelta(seconds=est_total)
    print(
        f"예상 소요 ~{est_total // 60}분 {est_total % 60}초 "
        f"(workers={args.workers}, extract_only={args.extract_only}) "
        f"→ 완료 {eta.strftime('%H:%M:%S')}"
    )

    results: list[dict] = []
    if args.workers <= 1:
        for entry in tqdm(samples, desc="final-batch"):
            results.append(
                _run_one(
                    entry,
                    out_root,
                    extract_only=args.extract_only,
                    llm_concurrency=args.llm_concurrency,
                )
            )
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futs = {
                pool.submit(
                    _run_one,
                    e,
                    out_root,
                    extract_only=args.extract_only,
                    llm_concurrency=args.llm_concurrency,
                ): e["id"]
                for e in samples
            }
            for fut in tqdm(as_completed(futs), total=len(futs), desc="final-batch"):
                results.append(fut.result())

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "ok": sum(1 for r in results if "error" not in r),
        "results": results,
    }
    (out_root / "index.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== final batch: {summary['ok']}/{summary['total']} ===")
    for r in results:
        if "error" in r:
            print(f"  {r['id']} — ERROR: {r['error']}")
        else:
            rep = r.get("report", {})
            rc = rep.get("recall")
            rc_s = f" recall={rc*100:.1f}%" if rc is not None else ""
            print(f"  {r['id']} — {rep.get('extracted_rows')} rows{rc_s}")
    print(f"\n출력: {out_root}")
    ok_ids = [r["id"] for r in results if "error" not in r]
    if ok_ids:
        paths = publish_domain_test(out_root, ok_ids)
        if paths:
            print(f"\ndomain_test ({len(paths)}개):")
            for p in paths:
                print(f"  {p}")
    return 0 if summary["ok"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
