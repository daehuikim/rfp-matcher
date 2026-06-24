"""PDF / HWPX / HWP → HTML + postprocess."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from app.domain.enums import DocumentMime
from app.domain.models import Document
from app.phase1.converters.html_postprocess import postprocess_html_file, raw_html_sibling


def _ensure_java() -> None:
    jdk = "/opt/homebrew/opt/openjdk@17/bin"
    if Path(jdk, "java").exists():
        os.environ["PATH"] = f"{jdk}:{os.environ.get('PATH', '')}"


def pdf_to_html(pdf: Path, workdir: Path) -> tuple[Path, Path | None, Path | None]:
    """반환: (postprocessed_html, raw_html|None, json_path|None)."""
    _ensure_java()
    import opendataloader_pdf

    workdir.mkdir(parents=True, exist_ok=True)
    for stale in list(workdir.glob("*.json")) + list(workdir.glob("*.html")):
        stale.unlink()
    opendataloader_pdf.convert(input_path=[str(pdf)], output_dir=str(workdir), format="json,html")
    json_path = next((p for p in workdir.glob("*.json")), None)
    html_path = next((p for p in workdir.glob("*.html")), None)
    if html_path is None:
        raise RuntimeError(f"opendataloader HTML 없음: {pdf}")
    postprocess_html_file(html_path)
    raw = raw_html_sibling(html_path)
    return html_path, raw if raw.is_file() else None, json_path


def native_to_html(source: Path, workdir: Path) -> tuple[Path, Path | None, Path | None, str]:
    """PDF·HWPX·HWP → HTML. 반환: (html, raw|None, json|None, converter_name)."""
    ext = source.suffix.lower()
    if ext == ".pdf":
        html, raw, json_path = pdf_to_html(source, workdir)
        return html, raw, json_path, "opendataloader-pdf"
    if ext == ".hwpx":
        from app.phase1.converters.hwpx_converter import HwpxConverter

        workdir.mkdir(parents=True, exist_ok=True)
        doc = Document(id=source.stem, src_path=source, mime=DocumentMime.HWPX)
        html_doc = asyncio.run(HwpxConverter().convert(doc, workdir))
        html_path = Path(html_doc.html_path)
        target = workdir / f"{source.stem}.html"
        if html_path != target:
            target.write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
            html_path = target
        postprocess_html_file(html_path)
        raw = raw_html_sibling(html_path)
        return html_path, raw if raw.is_file() else None, None, "HwpxConverter"
    if ext == ".hwp":
        from app.phase1.converters.hwp_converter import HwpConverter

        workdir.mkdir(parents=True, exist_ok=True)
        doc = Document(id=source.stem, src_path=source, mime=DocumentMime.HWP)
        html_doc = asyncio.run(HwpConverter().convert(doc, workdir))
        html_path = Path(html_doc.html_path)
        target = workdir / f"{source.stem}.html"
        if html_path != target:
            target.write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
            html_path = target
        postprocess_html_file(html_path)
        raw = raw_html_sibling(html_path)
        return html_path, raw if raw.is_file() else None, None, "HwpConverter"
    raise ValueError(f"지원하지 않는 원본 형식: {source}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# public_form 한글 RFP — PDF 페이지 절단 시 HWPX 원본 우선
_HWPX_ALT: dict[str, Path] = {
    "003-법제처": _repo_root() / "data/packages/005-법제처.hwpx",
}


def resolve_public_form_source(source: Path, sample_id: str = "") -> Path:
    """PDF 배치에서 명시 매핑·동일 stem HWPX만 우선 (다른 문서 오매칭 방지)."""
    if source.suffix.lower() != ".pdf":
        return source
    root = _repo_root()
    candidates: list[Path] = [
        source.with_suffix(".hwpx"),
        source.with_suffix(".hwp"),
    ]
    if sample_id and sample_id in _HWPX_ALT:
        candidates.append(_HWPX_ALT[sample_id])
    for alt in candidates:
        p = alt if alt.is_absolute() else root / alt
        if p.is_file():
            return p
    return source
