from __future__ import annotations

import asyncio
import logging
import re

import bm25s
from bm25s.tokenization import Tokenizer

from app.phase2.catalog.store import CatalogEntry
from app.phase2.vectorstore.base import SearchHit

logger = logging.getLogger(__name__)

_KOREAN_TOKEN = re.compile(r"[\w가-힣]{2,}")


def _korean_split(text: str) -> list[str]:
    return _KOREAN_TOKEN.findall(text.lower())


class Bm25CatalogRetriever:
    """
    KT 솔루션 카탈로그 BM25 검색 (bm25s).

    OpenAI embedding/Chroma 대체 — 로컬·무비용·한국어 키워드 매칭.
    카탈로그 규모(~수백 건)는 메모리 인덱스로 충분 — 디스크 persist 생략.
    """

    def __init__(self) -> None:
        self._retriever: bm25s.BM25 | None = None
        self._entries: list[CatalogEntry] = []
        self._tokenizer = Tokenizer(stemmer=None, stopwords=[], splitter=_korean_split)

    @property
    def indexed(self) -> bool:
        return self._retriever is not None and len(self._entries) > 0

    async def count(self) -> int:
        return len(self._entries)

    async def rebuild(self, entries: list[CatalogEntry]) -> int:
        if not entries:
            self._entries = []
            self._retriever = None
            return 0
        return await asyncio.to_thread(self._rebuild_sync, entries)

    def _rebuild_sync(self, entries: list[CatalogEntry]) -> int:
        self._entries = list(entries)
        texts = [e.embedding_text for e in self._entries]
        corpus_tokens = self._tokenizer.tokenize(texts)
        retriever = bm25s.BM25()
        retriever.index(corpus_tokens)
        self._retriever = retriever
        logger.info("BM25 카탈로그 인덱싱 완료: %d entries", len(entries))
        return len(entries)

    async def load(self) -> bool:
        """메모리 전용 — 디스크 로드 없음."""
        return self.indexed

    async def search(self, query: str, k: int = 10) -> list[SearchHit]:
        if not self._retriever or not self._entries:
            return []
        return await asyncio.to_thread(self._search_sync, query, k)

    def _search_sync(self, query: str, k: int) -> list[SearchHit]:
        assert self._retriever is not None
        query_tokens = self._tokenizer.tokenize([query])
        k = min(k, len(self._entries))
        if k <= 0:
            return []
        doc_ids, scores = self._retriever.retrieve(query_tokens, k=k)
        hits: list[SearchHit] = []
        for idx, score in zip(doc_ids[0], scores[0], strict=True):
            i = int(idx)
            if i < 0 or i >= len(self._entries):
                continue
            e = self._entries[i]
            hits.append(
                SearchHit(
                    id=e.id,
                    score=float(score),
                    text=e.embedding_text,
                    metadata={
                        "대분류": e.대분류,
                        "중분류": e.중분류,
                        "소분류": e.소분류,
                        "솔루션명": e.솔루션명,
                    },
                )
            )
        return hits
