from __future__ import annotations

from pathlib import Path

from app.domain.models import Document


def default_display_name(doc: Document) -> str:
    """원본 파일명(확장자 제외) 우선 — incoming UUID 파일명은 피한다."""
    if doc.display_name and doc.display_name.strip():
        return doc.display_name.strip()
    if doc.source_filename and doc.source_filename.strip():
        return Path(doc.source_filename).stem
    stem = doc.src_path.stem
    if len(stem) == 32 and all(c in "0123456789abcdef" for c in stem.lower()):
        return doc.src_path.name
    return stem


def resolve_document_title(doc: Document, *, manifest_source: str | None = None) -> str:
    if doc.display_name and doc.display_name.strip():
        return doc.display_name.strip()
    if doc.source_filename and doc.source_filename.strip():
        return Path(doc.source_filename).stem
    if manifest_source and manifest_source.strip():
        return Path(manifest_source).stem
    return default_display_name(doc)
