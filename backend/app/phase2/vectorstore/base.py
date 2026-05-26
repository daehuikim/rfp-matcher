from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class VectorRecord:
    id: str
    vector: list[float]
    text: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class SearchHit:
    id: str
    score: float
    text: str
    metadata: dict[str, str]


class VectorStore(ABC):
    """
    벡터 인덱스 추상.

    필수: 인스턴스는 **앱 lifespan 단일**. 멀티프로세스 환경에선 서버 모드의 클라이언트 stub만
    각 워커에 두고, 인덱스 자체는 하나의 프로세스/컨테이너에만 존재해야 한다.

    동시성: 쓰기는 `_write_lock`으로 직렬화. 읽기(search)는 락 없이 동시 허용.
    """

    def __init__(self) -> None:
        self._write_lock = asyncio.Lock()

    @abstractmethod
    async def upsert(self, records: list[VectorRecord]) -> None: ...

    @abstractmethod
    async def search(self, vector: list[float], k: int = 5) -> list[SearchHit]: ...

    @abstractmethod
    async def count(self) -> int: ...
