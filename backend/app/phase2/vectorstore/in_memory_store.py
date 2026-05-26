from __future__ import annotations

import math

from .base import SearchHit, VectorRecord, VectorStore


class InMemoryVectorStore(VectorStore):
    """
    코사인 유사도 기반 in-memory 인덱스. MVP·테스트용.

    Chroma 서버 모드로 옮겨갈 때 인터페이스는 동일하므로 호출 코드는 영향 없음.
    """

    def __init__(self) -> None:
        super().__init__()
        self._records: dict[str, VectorRecord] = {}

    async def upsert(self, records: list[VectorRecord]) -> None:
        async with self._write_lock:
            for r in records:
                self._records[r.id] = r

    async def search(self, vector: list[float], k: int = 5) -> list[SearchHit]:
        q_norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        hits: list[SearchHit] = []
        for r in self._records.values():
            dot = sum(a * b for a, b in zip(vector, r.vector, strict=False))
            r_norm = math.sqrt(sum(x * x for x in r.vector)) or 1.0
            score = dot / (q_norm * r_norm)
            hits.append(SearchHit(id=r.id, score=score, text=r.text, metadata=r.metadata))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    async def count(self) -> int:
        return len(self._records)
