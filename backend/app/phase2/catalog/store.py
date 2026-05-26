from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CatalogEntry(BaseModel):
    """KT AI 솔루션 카탈로그의 한 항목 (대분류·중분류·소분류 + 메타)."""

    id: str  # 안정 식별자(슬러그)
    대분류: str
    중분류: str
    소분류: str
    솔루션명: str
    설명: str
    강점: list[str] = Field(default_factory=list)
    한계: list[str] = Field(default_factory=list)
    레퍼런스: list[str] = Field(default_factory=list)

    @property
    def embedding_text(self) -> str:
        """임베딩 입력 — 모든 메타를 한 문자열로 직렬화."""
        parts = [
            f"[{self.대분류} > {self.중분류} > {self.소분류}]",
            f"솔루션: {self.솔루션명}",
            self.설명,
            "강점: " + " · ".join(self.강점) if self.강점 else "",
            "한계: " + " · ".join(self.한계) if self.한계 else "",
            "레퍼런스: " + " · ".join(self.레퍼런스) if self.레퍼런스 else "",
        ]
        return "\n".join(p for p in parts if p)


class CatalogStore:
    """JSON 파일 기반 카탈로그 저장소. 1회 크롤링 산출물을 사람이 검수한 뒤 커밋."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: list[CatalogEntry] = []

    @classmethod
    def load(cls, path: Path) -> Self:
        inst = cls(path)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            inst._entries = [CatalogEntry.model_validate(item) for item in raw]
            logger.info("카탈로그 로드: %d entries", len(inst._entries))
        return inst

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                [e.model_dump() for e in self._entries],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @property
    def entries(self) -> list[CatalogEntry]:
        return list(self._entries)

    def replace(self, entries: list[CatalogEntry]) -> None:
        # 동일 id 합치기 — 중복 방어
        by_id = {e.id: e for e in entries}
        self._entries = list(by_id.values())

    def __len__(self) -> int:
        return len(self._entries)
