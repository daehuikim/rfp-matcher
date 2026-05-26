from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .base import SearchHit, VectorRecord, VectorStore

logger = logging.getLogger(__name__)


class ChromaStore(VectorStore):
    """
    Chroma 어댑터. 단일 인스턴스 — lifespan에서 1회 생성해 모든 요청이 공유.

    persistence는 `persist_directory`에 sqlite + parquet. 멀티프로세스로 확장하려면
    Chroma server 모드(별도 컨테이너)로 옮기고 client만 워커에서 만들 것.
    """

    def __init__(self, persist_directory: Path, collection: str = "kt_catalog") -> None:
        super().__init__()
        import chromadb

        persist_directory.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_directory))
        self._col = self._client.get_or_create_collection(collection)

    async def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        async with self._write_lock:
            self._col.upsert(
                ids=[r.id for r in records],
                embeddings=[r.vector for r in records],
                documents=[r.text for r in records],
                metadatas=[r.metadata for r in records],
            )

    async def search(self, vector: list[float], k: int = 5) -> list[SearchHit]:
        result: dict[str, Any] = self._col.query(query_embeddings=[vector], n_results=k)
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        dists = result.get("distances", [[]])[0]
        out: list[SearchHit] = []
        for i, doc, meta, dist in zip(ids, docs, metas, dists, strict=False):
            # chroma는 거리(작을수록 가까움); 0~2 범위. 점수로 환산.
            score = 1.0 - (float(dist) / 2.0)
            out.append(SearchHit(id=i, score=score, text=doc or "", metadata=dict(meta or {})))
        return out

    async def count(self) -> int:
        return int(self._col.count())
