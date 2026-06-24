#!/usr/bin/env python3
"""manifest 샘플 → data/packages/{NNN}-{기관명}.{원본|xlsx} 페어 생성."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
PACKAGES = ROOT / "data" / "packages"
PACKAGE_LOGS = PACKAGES / "logs"
MANIFEST = ROOT / "data" / "samples.manifest.json"
WORK = ROOT / "temp" / "package_build"

# package_base → 정답 조견표 (data/processed)
GOLD_SOURCES: dict[str, Path] = {
    "003-신한라이프": PROCESSED / "(QA)신한라이프 AX HUB 구축 사업_0428.xlsx",
    "004-JB금융": PROCESSED / "JB금융그룹 공동 AI Agent Platform 구축 요건.xlsx",
    "005-법제처": PROCESSED / "법제처_요구사항_정리_개선.xlsx",
}

sys.path.insert(0, str(BACKEND))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_packages")

_EXT_MIME = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".hwp": "application/x-hwp",
    ".hwpx": "application/vnd.hancom.hwpx",
    ".html": "text/html",
    ".htm": "text/html",
}


def _resolve_raw(filename: str) -> Path | None:
    direct = RAW / filename
    if direct.is_file():
        return direct
    target = filename.lower()
    for p in RAW.iterdir():
        if p.name.lower() == target or p.name == filename:
            return p
    return None


def _slug_from_label(filename: str, labels: dict[str, str]) -> str:
    label = labels.get(filename, Path(filename).stem)
    org = label.split("·")[0].strip()
    s = re.sub(r'[\\/?*[\]:<>|"]', "", org)
    s = re.sub(r"\s+", "", s)
    return s or Path(filename).stem


def _hwp_to_html(hwp: Path, out_dir: Path) -> Path:
    hwp5html = shutil.which("hwp5html")
    if not hwp5html:
        raise RuntimeError("hwp5html 없음 — pip install pyhwp")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    proc = subprocess.run(
        [hwp5html, "--output", str(out_dir), str(hwp)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"hwp5html 실패: {proc.stderr[:600]}")
    produced = out_dir / "index.xhtml"
    if not produced.is_file():
        raise RuntimeError("hwp5html HTML 미생성")
    dest = out_dir / f"{hwp.stem}.html"
    shutil.copyfile(produced, dest)
    return dest


async def _convert_to_html(src: Path, work_dir: Path) -> tuple[Path, bool]:
    """앱 컨버터로 HTML 변환. 반환: (html_path, korean_hwp)."""
    ext = src.suffix.lower()
    if ext in (".html", ".htm"):
        return src, True
    if ext == ".hwp":
        return _hwp_to_html(src, work_dir), True

    from app.core.config import get_settings
    from app.domain.enums import DocumentMime
    from app.domain.models import Document
    from app.phase1.converters.registry import select_converter

    mime_str = _EXT_MIME.get(ext)
    if not mime_str:
        raise ValueError(f"지원하지 않는 확장자: {ext}")
    mime = DocumentMime(mime_str)
    settings = get_settings()
    converter = select_converter(mime, settings)
    work_dir.mkdir(parents=True, exist_ok=True)

    doc = Document(
        id=f"pkg-{src.stem}",
        source_filename=src.name,
        src_path=src,
        mime=mime,
    )
    html_doc = await converter.convert(doc, work_dir)
    korean = mime in (DocumentMime.HWP, DocumentMime.HWPX)
    return Path(html_doc.html_path), korean


def _pipeline_input(src: Path, work_dir: Path) -> tuple[str, bool]:
    ext = src.suffix.lower()
    if ext == ".pdf":
        return str(src), False
    if ext in (".html", ".htm"):
        return str(src), True
    html_path, korean = asyncio.run(_convert_to_html(src, work_dir))
    return str(html_path), korean


def _run_v2(src: Path, work_dir: Path, pkg_base: str) -> Path:
    from prototype.v2.pipeline import run as v2_run
    from app.services.pipeline_logger import PipelineLogSession

    log_session = PipelineLogSession(
        PACKAGE_LOGS,
        run_id=pkg_base,
        source_path=src,
        engine="v2",
    )
    v2_input, korean_hwp = _pipeline_input(src, work_dir)
    log.info("V2 추출: %s (korean_hwp=%s)", src.name, korean_hwp)
    manifest = v2_run(
        v2_input,
        None,
        mode="llm",
        tab_mode="ordered",
        korean_hwp=korean_hwp,
        log_session=log_session,
    )
    entry_log = PACKAGE_LOGS / pkg_base / "pipeline.json"
    if entry_log.is_file():
        log.info("파이프라인 로그: %s", entry_log.parent)
    return Path(manifest["artifacts"]["requirements_xlsx"])


def build_all() -> int:
    meta = json.loads(MANIFEST.read_text(encoding="utf-8"))
    order: list[str] = meta["order"]
    labels: dict[str, str] = meta["labels"]

    PACKAGES.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    # 기존 패키지 정리 후 재생성
    for old in PACKAGES.glob("*"):
        if old.is_file():
            old.unlink()

    results: list[dict] = []
    for idx, filename in enumerate(tqdm(order, desc="packages"), start=1):
        src = _resolve_raw(filename)
        slug = _slug_from_label(filename, labels)
        num = f"{idx:03d}"
        ext = Path(filename).suffix.lower()
        pkg_base = f"{num}-{slug}"
        entry: dict = {
            "index": idx,
            "manifest_file": filename,
            "label": labels.get(filename, ""),
            "package_base": pkg_base,
        }

        if src is None:
            entry["error"] = f"raw 파일 없음: {filename}"
            log.error(entry["error"])
            results.append(entry)
            continue

        work = WORK / pkg_base
        work.mkdir(parents=True, exist_ok=True)
        try:
            src_dest = PACKAGES / f"{pkg_base}{ext}"
            shutil.copy2(src, src_dest)
            entry["source"] = str(src_dest)

            xlsx_src = _run_v2(src, work, pkg_base)
            xlsx_dest = PACKAGES / f"{pkg_base}.xlsx"
            shutil.copy2(xlsx_src, xlsx_dest)
            entry["xlsx"] = str(xlsx_dest)
            log_dir = PACKAGE_LOGS / pkg_base
            if log_dir.is_dir():
                entry["pipeline_log"] = str(log_dir / "pipeline.json")
            v2_manifest = xlsx_src.parent / "manifest.json"
            if v2_manifest.is_file():
                m = json.loads(v2_manifest.read_text(encoding="utf-8"))
                entry["rows"] = m.get("report", {}).get("extracted_rows")
                entry["steps"] = m.get("steps", [])[-3:]

            gold_src = GOLD_SOURCES.get(pkg_base)
            if gold_src and gold_src.is_file():
                gold_dest = PACKAGES / f"{pkg_base}-gold.xlsx"
                shutil.copy2(gold_src, gold_dest)
                entry["gold_xlsx"] = str(gold_dest)

            log.info("완료 %s — %s + %s", pkg_base, src_dest.name, xlsx_dest.name)
        except Exception as e:
            entry["error"] = str(e)
            log.error("%s 실패: %s", pkg_base, e)
        results.append(entry)

    summary = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "total": len(order),
        "ok": sum(1 for r in results if "xlsx" in r),
        "failed": sum(1 for r in results if "error" in r),
        "packages": results,
    }
    (PACKAGES / "packages.manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n=== packages 빌드 완료: {summary['ok']}/{summary['total']} ===")
    for r in results:
        if "xlsx" in r:
            gold = f" + {r['package_base']}-gold.xlsx" if r.get("gold_xlsx") else ""
            print(f"  {r['package_base']}{Path(r['manifest_file']).suffix} + {r['package_base']}.xlsx{gold}")
        else:
            print(f"  {r['package_base']} — ERROR: {r.get('error')}")
    print(f"\n출력: {PACKAGES}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(build_all())
