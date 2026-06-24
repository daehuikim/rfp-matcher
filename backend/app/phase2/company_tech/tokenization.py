from __future__ import annotations

import re

from app.phase2.company_tech.models import ChunkRecord

_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣:+#._-]{2,}")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def build_bm25_text(record: ChunkRecord) -> str:
    metadata = record.metadata
    lexical_text = metadata.get("lexical_text")
    if lexical_text:
        return str(lexical_text)

    topics = metadata.get("topics") or []
    keywords = metadata.get("keywords") or []
    if isinstance(topics, list):
        topics = " ".join(str(t) for t in topics)
    if isinstance(keywords, list):
        keywords = " ".join(str(k) for k in keywords)

    parts = [
        str(metadata.get("source_file", "")),
        str(metadata.get("section_title", "")),
        str(topics),
        str(keywords),
        record.document,
    ]
    return "\n".join(part for part in parts if part)
