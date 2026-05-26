from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.domain.enums import DocumentMime
from app.domain.models import Document

# 확장자 → MIME (간단 매핑, 실제 sniff은 magic bytes로 보강 가능)
EXT_TO_MIME: dict[str, DocumentMime] = {
    ".pdf": DocumentMime.PDF,
    ".doc": DocumentMime.DOC,
    ".docx": DocumentMime.DOCX,
    ".hwpx": DocumentMime.HWPX,
}


class DocumentLoader(ABC):
    """포맷별 로더의 공통 인터페이스 — 파일 핸들·메타데이터까지 책임."""

    mime: DocumentMime

    @abstractmethod
    async def load(self, src_path: Path) -> Document: ...


class GenericLoader(DocumentLoader):
    """확장자 기반 MIME만 붙여 Document를 만드는 기본 로더."""

    def __init__(self, mime: DocumentMime) -> None:
        self.mime = mime

    async def load(self, src_path: Path) -> Document:
        return Document(id=uuid.uuid4().hex, src_path=src_path, mime=self.mime)


def select_loader(src_path: Path) -> DocumentLoader:
    mime = EXT_TO_MIME.get(src_path.suffix.lower())
    if mime is None:
        raise ValueError(f"지원하지 않는 확장자: {src_path.suffix}")
    return GenericLoader(mime)
