from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI
from pydantic import BaseModel, Field


DEFAULT_COLLECTION = "company_tech_docs"
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200
_RECURSIVE_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
DEFAULT_METADATA_MODEL = "gpt-5.4-mini"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_BATCH_SIZE = 32


class ChunkInsight(BaseModel):
    section_title: str = Field(
        default="",
        description="Best short title for the chunk. Return empty string if unclear.",
    )
    topics: list[str] = Field(
        default_factory=list,
        description="Up to 5 normalized topic labels for retrieval filtering.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Up to 8 important nouns or feature names from the chunk.",
    )


@dataclass
class TextChunk:
    chunk_id: str
    source_file: str
    chunk_index: int
    char_start: int
    char_end: int
    text: str
    raw_section_title: str


def load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def infer_section_title(text: str) -> str:
    for line in text.splitlines():
        candidate = " ".join(line.split()).strip(" -:\t")
        if candidate:
            return candidate[:120]
    return ""


def _build_text_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=_RECURSIVE_SEPARATORS,
        length_function=len,
    )


def _locate_chunk_spans(
    normalized: str,
    chunk_texts: Sequence[str],
    chunk_overlap: int,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    search_from = 0
    for chunk_text in chunk_texts:
        idx = normalized.find(chunk_text, search_from)
        if idx == -1:
            idx = search_from
        spans.append((idx, idx + len(chunk_text)))
        search_from = max(0, idx + len(chunk_text) - chunk_overlap)
    return spans


def split_text_into_chunks(
    text: str,
    source_file: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[TextChunk]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    splitter = _build_text_splitter(chunk_size, chunk_overlap)
    chunk_texts = splitter.split_text(normalized)
    spans = _locate_chunk_spans(normalized, chunk_texts, chunk_overlap)

    chunks: list[TextChunk] = []
    for chunk_index, (chunk_text, (char_start, char_end)) in enumerate(zip(chunk_texts, spans, strict=True)):
        if not chunk_text:
            continue
        chunks.append(
            TextChunk(
                chunk_id=f"{source_file}:{chunk_index}",
                source_file=source_file,
                chunk_index=chunk_index,
                char_start=char_start,
                char_end=char_end,
                text=chunk_text,
                raw_section_title=infer_section_title(chunk_text),
            )
        )
    return chunks


def iter_text_files(data_dir: Path, pattern: str) -> list[Path]:
    return sorted(path for path in data_dir.glob(pattern) if path.is_file())


def fallback_insight(chunk: TextChunk) -> ChunkInsight:
    words = re.findall(r"[A-Za-z0-9가-힣:+_-]{2,}", chunk.text)
    deduped: list[str] = []
    seen: set[str] = set()
    for word in words:
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(word)
        if len(deduped) >= 8:
            break

    return ChunkInsight(
        section_title=chunk.raw_section_title,
        topics=deduped[:5],
        keywords=deduped[:8],
    )


def extract_chunk_insight(client: OpenAI, model: str, chunk: TextChunk) -> ChunkInsight:
    prompt = (
        "Extract retrieval metadata for a chunk from a Korean technology document.\n"
        "Rules:\n"
        "1. topics should be short normalized labels useful for retrieval filters.\n"
        "2. keywords should preserve important product names, feature names, or domain terms.\n"
        "3. If a section title is unclear, return an empty string.\n"
        "4. Do not invent facts that are not in the chunk."
    )

    response = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"source_file: {chunk.source_file}\n"
                    f"raw_section_title: {chunk.raw_section_title}\n"
                    f"chunk_text:\n{chunk.text}"
                ),
            },
        ],
        text_format=ChunkInsight,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise ValueError(f"Structured output parsing failed for {chunk.chunk_id}")

    parsed.topics = normalize_list(parsed.topics, limit=5)
    parsed.keywords = normalize_list(parsed.keywords, limit=8)
    parsed.section_title = " ".join(parsed.section_title.split())
    return parsed


def normalize_list(values: Sequence[str], limit: int) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split()).strip(" ,")
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned[:80])
        if len(normalized) >= limit:
            break
    return normalized


def build_embedding_input(chunk: TextChunk, insight: ChunkInsight) -> str:
    title = insight.section_title or chunk.raw_section_title or chunk.source_file
    topics = ", ".join(insight.topics)
    keywords = ", ".join(insight.keywords)
    parts = [
        f"Source file: {chunk.source_file}",
        f"Section: {title}",
    ]

    if topics:
        parts.append(f"Topics: {topics}")
    if keywords:
        parts.append(f"Keywords: {keywords}")

    parts.append("Original text:")
    parts.append(chunk.text)
    return "\n".join(parts)


def build_lexical_text(chunk: TextChunk, insight: ChunkInsight) -> str:
    title = insight.section_title or chunk.raw_section_title or chunk.source_file
    parts = [
        chunk.source_file,
        title,
        " ".join(insight.topics),
        " ".join(insight.keywords),
        chunk.text,
    ]
    return "\n".join(part for part in parts if part)


def batch_iter(items: Sequence[str], batch_size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def create_embeddings(
    client: OpenAI,
    model: str,
    texts: Sequence[str],
    batch_size: int,
) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for batch in batch_iter(texts, batch_size):
        response = client.embeddings.create(
            model=model,
            input=list(batch),
            encoding_format="float",
        )
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


def build_metadata(chunk: TextChunk, insight: ChunkInsight) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source_file": chunk.source_file,
        "chunk_index": chunk.chunk_index,
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
        "lexical_text": build_lexical_text(chunk, insight),
    }

    section_title = insight.section_title or chunk.raw_section_title
    if section_title:
        metadata["section_title"] = section_title
    if insight.topics:
        metadata["topics"] = insight.topics
    if insight.keywords:
        metadata["keywords"] = insight.keywords

    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a Chroma vector database from text files with LLM metadata extraction."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "data" / "database",
    )
    parser.add_argument("--glob", default="*.txt")
    parser.add_argument(
        "--persist-dir",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "data" / "chroma_db",
    )
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    parser.add_argument("--metadata-model", default=DEFAULT_METADATA_MODEL)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--reset-collection", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env_path = Path(__file__).resolve().parents[3] / "config" / ".env"
    load_dotenv(env_path)

    if "OPENAI_API_KEY" not in os.environ:
        raise EnvironmentError("OPENAI_API_KEY is not set. Add it to the environment or .env.")

    files = iter_text_files(args.data_dir, args.glob)
    if not files:
        raise FileNotFoundError(f"No files matched {args.glob!r} in {args.data_dir}")

    client = OpenAI()
    chroma_client = chromadb.PersistentClient(path=str(args.persist_dir))

    if args.reset_collection:
        try:
            chroma_client.delete_collection(args.collection)
            print(f"Deleted existing collection: {args.collection}")
        except Exception:
            pass

    collection = chroma_client.get_or_create_collection(name=args.collection)

    all_chunks: list[TextChunk] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        chunks = split_text_into_chunks(
            text=text,
            source_file=path.name,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        print(f"{path.name}: {len(chunks)} chunks")
        all_chunks.extend(chunks)

    if not all_chunks:
        raise RuntimeError("Chunking produced no content.")

    insights: list[ChunkInsight] = []
    for index, chunk in enumerate(all_chunks, start=1):
        print(f"[{index}/{len(all_chunks)}] extracting metadata: {chunk.chunk_id}")
        try:
            insight = extract_chunk_insight(client, args.metadata_model, chunk)
        except Exception as exc:
            print(f"  metadata extraction failed, using fallback for {chunk.chunk_id}: {exc}")
            insight = fallback_insight(chunk)
        insights.append(insight)

    embedding_inputs = [
        build_embedding_input(chunk, insight)
        for chunk, insight in zip(all_chunks, insights, strict=True)
    ]
    print(f"Creating embeddings for {len(embedding_inputs)} chunks...")
    embeddings = create_embeddings(
        client=client,
        model=args.embedding_model,
        texts=embedding_inputs,
        batch_size=args.embedding_batch_size,
    )

    ids = [chunk.chunk_id for chunk in all_chunks]
    documents = [chunk.text for chunk in all_chunks]
    metadatas = [
        build_metadata(chunk, insight)
        for chunk, insight in zip(all_chunks, insights, strict=True)
    ]

    collection.upsert(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    print(
        f"Upserted {len(ids)} chunks into '{args.collection}' at {args.persist_dir.resolve()}"
    )


if __name__ == "__main__":
    main()
