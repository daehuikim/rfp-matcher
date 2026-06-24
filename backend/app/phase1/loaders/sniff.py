"""매직 바이트 기반 실제 포맷 감지 — 확장자와 불일치 시 보정."""
from __future__ import annotations

from pathlib import Path

from app.domain.enums import DocumentMime

_OLE = b"\xd0\xcf\x11\xe0"
_PDF = b"%PDF"
_ZIP = b"PK"


def sniff_mime(path: Path) -> DocumentMime | None:
  head = path.read_bytes()[:8]
  if head.startswith(_PDF):
    return DocumentMime.PDF
  if head.startswith(_ZIP):
    ext = path.suffix.lower()
    if ext == ".hwpx":
      return DocumentMime.HWPX
    return DocumentMime.DOCX
  if head.startswith(_OLE):
    return DocumentMime.DOC
  return None


def resolve_mime(path: Path) -> DocumentMime:
  from .base import EXT_TO_MIME

  ext = path.suffix.lower()
  by_ext = EXT_TO_MIME.get(ext)
  sniffed = sniff_mime(path)
  if sniffed is None:
    if by_ext is None:
      raise ValueError(f"지원하지 않는 확장자: {ext}")
    return by_ext
  # 확장자 .docx 인데 OLE → 구형 .doc
  if by_ext == DocumentMime.DOCX and sniffed == DocumentMime.DOC:
    return DocumentMime.DOC
  if by_ext == DocumentMime.DOC and sniffed == DocumentMime.DOCX:
    return DocumentMime.DOCX
  return sniffed if by_ext is None else by_ext
