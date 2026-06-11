#!/usr/bin/env python3
"""기존 artifacts 캐시에서 가능한 범위의 파이프라인 로그를 backfill."""
from __future__ import annotations

import json
import logging
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
ARTIFACTS = ROOT / "data" / "artifacts"
LOGS = ROOT / "data" / "packages" / "logs"

sys.path.insert(0, str(BACKEND))

from app.phase1.converters.html_postprocess import postprocess_html_file, raw_html_sibling
from app.services.pipeline_logger import PipelineLogSession, write_step_readme

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backfill_logs")

_STYLE_RE = re.compile(r"""style\s*=\s*['"]""")


def _looks_like_raw_opendataloader(html: str) -> bool:
    return bool(_STYLE_RE.search(html)) or "<span " in html


def backfill_bucket(bucket: Path) -> bool:
    manifest_path = bucket / "manifest.json"
    if not manifest_path.is_file():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_id = bucket.name
    source_name = manifest.get("source_name") or run_id
    content_hash = manifest.get("content_hash")

    session = PipelineLogSession(
        LOGS,
        run_id=run_id,
        source_name=source_name,
        content_hash=content_hash,
        engine="v2",
    )

    session.record_step(
        "00_source",
        "원본 업로드",
        description="backfill — 원본 파일은 캐시에 미보존",
        meta={"source_name": source_name, "note": "backfill"},
    )

    converted = bucket / "converted.html"
    if converted.is_file():
        html_text = converted.read_text(encoding="utf-8", errors="replace")
        is_raw = _looks_like_raw_opendataloader(html_text)
        mime = manifest.get("mime", "")

        if is_raw:
            session.copy_file(converted, "01_convert_raw", "source.html", role="output")
            session.record_step(
                "01_convert_raw",
                "변환 (raw)",
                description="opendataloader-pdf raw HTML (기존 캐시 — 후처리 전)",
                outputs=[
                    {
                        "path": str(converted),
                        "name": "source.html",
                        "role": "output",
                        "step": "01_convert_raw",
                        "size_bytes": converted.stat().st_size,
                        "exists": True,
                        "note": "backfill — artifact converted.html 는 raw였음",
                    }
                ],
                meta={"converter": "opendataloader-pdf", "backfill": True},
            )

            post_copy = session.step_dir("02_convert_postprocessed") / "source.html"
            shutil.copy2(converted, post_copy)
            postprocess_html_file(post_copy)
            raw_backup = raw_html_sibling(post_copy)
            session.record_convert_postprocessed(
                post_html=post_copy,
                raw_html=raw_backup if raw_backup.is_file() else None,
                converter="opendataloader-pdf",
            )
            post_text = post_copy.read_text(encoding="utf-8", errors="replace")
            log.info(
                "%s: raw style=%s → postprocessed style=%s",
                run_id,
                "style=" in html_text,
                "style=" in post_text,
            )
        else:
            session.copy_file(converted, "02_convert_postprocessed", "converted.html", role="output")
            session.record_step(
                "02_convert_postprocessed",
                "HTML (캐시)",
                description="artifact converted.html (이미 후처리됨)",
                outputs=[
                    {
                        "path": str(converted),
                        "name": "converted.html",
                        "role": "output",
                        "step": "02_convert_postprocessed",
                        "size_bytes": converted.stat().st_size,
                        "exists": True,
                    }
                ],
                meta={"mime": mime, "backfill": True},
            )

    req_path = bucket / "requirements.json"
    req_count = manifest.get("requirements_count", 0)
    snapshot = manifest.get("pipeline_snapshot") or {}
    history = snapshot.get("history") or []
    extract_steps = [
        h.get("stage", "")
        for h in history
        if h.get("stage") not in ("ATOMIZING", "RECOMMENDING")
    ][-8:] or ["(캐시 복원 — V2 단계 상세 미보존)"]

    session.record_extract(
        mode="llm",
        pipeline_steps=extract_steps,
        row_count=req_count,
        meta={"backfill": True, "pipeline_snapshot_stages": len(history)},
    )

    if req_path.is_file():
        session.copy_file(req_path, "04_output", "requirements.json", role="output")
        session.record_step(
            "04_output",
            "최종 산출 (JSON)",
            description="artifact 캐시 requirements.json — Excel은 재생성 필요",
            outputs=[
                {
                    "path": str(req_path),
                    "name": "requirements.json",
                    "role": "output",
                    "step": "04_output",
                    "size_bytes": req_path.stat().st_size,
                    "exists": True,
                }
            ],
            meta={"requirements_count": req_count},
        )

    session.finalize(extra={"backfill": True, "artifact_bucket": str(bucket)})
    write_step_readme(session.log_dir)
    session.mirror_to(bucket)
    log.info("backfill OK: %s → %s", bucket.name, session.log_dir)
    return True


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    if not ARTIFACTS.is_dir():
        log.error("artifacts 디렉터리 없음: %s", ARTIFACTS)
        return 1

    ok = 0
    for bucket in sorted(ARTIFACTS.iterdir()):
        if not bucket.is_dir():
            continue
        if backfill_bucket(bucket):
            ok += 1

    log.info("완료: %d buckets backfill → %s", ok, LOGS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
