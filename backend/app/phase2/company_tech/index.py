from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import bm25s
import chromadb
from bm25s.tokenization import Tokenizer
from openai import AsyncOpenAI

from app.core.config import Settings
from app.phase2.company_tech.models import ChunkRecord, SearchResult
from app.phase2.company_tech.tokenization import build_bm25_text, tokenize

if TYPE_CHECKING:
    from chromadb.api.models.Collection import Collection

logger = logging.getLogger(__name__)


def _tokenize_list(text: str) -> list[str]:
    return tokenize(text)


class CompanyTechIndex:
    """Chroma 벡터 DB + 청크 BM25 인덱스."""

    def __init__(
        self,
        collection: Collection,
        records: list[ChunkRecord],
        bm25: bm25s.BM25,
        tokenizer: Tokenizer,
    ) -> None:
        self._collection = collection
        self._records = records
        self._bm25 = bm25
        self._tokenizer = tokenizer
        self._id_to_index = {r.id: i for i, r in enumerate(records)}

    @property
    def ready(self) -> bool:
        return len(self._records) > 0

    @property
    def chunk_count(self) -> int:
        return len(self._records)

    def source_files(self) -> list[str]:
        names = {
            str(r.metadata.get("source_file"))
            for r in self._records
            if r.metadata.get("source_file")
        }
        return sorted(names)

    @classmethod
    async def load(cls, settings: Settings) -> CompanyTechIndex | None:
        return await asyncio.to_thread(cls._load_sync, settings)

    @classmethod
    def _load_sync(cls, settings: Settings) -> CompanyTechIndex | None:
        persist_dir = settings.chroma_persist_dir
        if not persist_dir.exists():
            logger.warning("Chroma persist 디렉터리 없음: %s", persist_dir)
            return None
        try:
            client = chromadb.PersistentClient(path=str(persist_dir))
            collection = client.get_collection(name=settings.chroma_collection)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chroma collection 로드 실패: %s", exc)
            return None

        result = collection.get(include=["documents", "metadatas"])
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        if not ids:
            logger.warning("Chroma collection 비어 있음: %s", settings.chroma_collection)
            return None

        records = [
            ChunkRecord(id=chunk_id, document=doc or "", metadata=meta or {})
            for chunk_id, doc, meta in zip(ids, documents, metadatas, strict=True)
        ]
        tokenizer = Tokenizer(stemmer=None, stopwords=[], splitter=_tokenize_list)
        corpus_tokens = tokenizer.tokenize([build_bm25_text(r) for r in records])
        bm25 = bm25s.BM25()
        bm25.index(corpus_tokens)
        logger.info(
            "CompanyTechIndex 준비: %d chunks, sources=%s",
            len(records),
            sorted({r.metadata.get("source_file") for r in records}),
        )
        return cls(collection=collection, records=records, bm25=bm25, tokenizer=tokenizer)

    async def create_query_embedding(
        self,
        api_key: str,
        model: str,
        query: str,
    ) -> list[float]:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.embeddings.create(
            model=model,
            input=query,
            encoding_format="float",
        )
        return list(response.data[0].embedding)

    @staticmethod
    def _build_source_where(source_files: list[str]) -> dict | None:
        if len(source_files) == 1:
            return {"source_file": source_files[0]}
        if len(source_files) > 1:
            return {"$or": [{"source_file": sf} for sf in source_files]}
        return None

    async def vector_search(
        self,
        query: str,
        top_k: int,
        embedding_model: str,
        api_key: str,
        source_files: list[str],
    ) -> list[SearchResult]:
        embedding = await self.create_query_embedding(api_key, embedding_model, query)
        return await asyncio.to_thread(
            self._vector_query_sync,
            embedding,
            top_k,
            source_files,
        )

    def _vector_query_sync(
        self,
        embedding: list[float],
        top_k: int,
        source_files: list[str],
    ) -> list[SearchResult]:
        kwargs: dict = {
            "query_embeddings": [embedding],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        where = self._build_source_where(source_files)
        if where:
            kwargs["where"] = where
        results = self._collection.query(**kwargs)
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        return [
            SearchResult(
                chunk_id=chunk_id,
                document=document or "",
                metadata=metadata or {},
                vector_rank=rank,
                vector_distance=float(distance),
            )
            for rank, (chunk_id, document, metadata, distance) in enumerate(
                zip(ids, documents, metadatas, distances, strict=True),
                start=1,
            )
        ]

    async def bm25_search(
        self,
        query: str,
        top_k: int,
        source_files: list[str],
    ) -> list[SearchResult]:
        return await asyncio.to_thread(self._bm25_search_sync, query, top_k, source_files)

    def _bm25_search_sync(
        self,
        query: str,
        top_k: int,
        source_files: list[str],
    ) -> list[SearchResult]:
        query_tokens = self._tokenizer.tokenize([query])
        allowed = set(source_files)
        k = min(top_k, len(self._records))
        if k <= 0:
            return []
        doc_ids, scores = self._bm25.retrieve(query_tokens, k=k)
        out: list[SearchResult] = []
        rank = 0
        for idx, score in zip(doc_ids[0], scores[0], strict=True):
            i = int(idx)
            if i < 0 or i >= len(self._records):
                continue
            record = self._records[i]
            source_file = record.metadata.get("source_file")
            if allowed and source_file not in allowed:
                continue
            if float(score) <= 0:
                continue
            rank += 1
            out.append(
                SearchResult(
                    chunk_id=record.id,
                    document=record.document,
                    metadata=record.metadata,
                    bm25_rank=rank,
                    bm25_score=float(score),
                )
            )
            if len(out) >= top_k:
                break
        return out

    async def hybrid_search(
        self,
        query: str,
        top_k: int,
        embedding_model: str,
        api_key: str,
        source_files: list[str],
    ) -> list[SearchResult]:
        vector_results = await self.vector_search(
            query, top_k, embedding_model, api_key, source_files
        )
        bm25_results = await self.bm25_search(query, top_k, source_files)
        return dedupe_results(vector_results + bm25_results, limit=top_k * 2)


def dedupe_results(results: list[SearchResult], limit: int) -> list[SearchResult]:
    deduped: list[SearchResult] = []
    seen: set[str] = set()
    for result in results:
        if result.chunk_id in seen:
            continue
        seen.add(result.chunk_id)
        deduped.append(result)
        if len(deduped) >= limit:
            break
    return deduped
