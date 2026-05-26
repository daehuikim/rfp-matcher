from __future__ import annotations

import pytest

from app.phase2.catalog.canonicalizer import (
    CatalogDuplicateReason,
    canonicalize_catalog_entries,
    content_fingerprint,
    embedding_text_similarity,
    resolve_catalog_id,
)
from app.phase2.catalog.store import CatalogEntry


def _entry(
    eid: str,
    *,
    major: str = "K RAG",
    mid: str = "IntelliSearch",
    sub: str = "임베딩 검색",
    name: str = "K RAG · IntelliSearch",
    desc: str = "설명",
) -> CatalogEntry:
    return CatalogEntry(
        id=eid,
        대분류=major,
        중분류=mid,
        소분류=sub,
        솔루션명=name,
        설명=desc,
        강점=["a"],
        한계=["b"],
        레퍼런스=[],
    )


def test_exact_duplicate_merged() -> None:
    a = _entry("id-a", desc="동일 본문")
    b = _entry("id-b", desc="동일 본문")
    assert content_fingerprint(a) == content_fingerprint(b)
    result = canonicalize_catalog_entries([a, b])
    assert len(result.entries) == 1
    assert result.alias_map["id-b"] == "id-a"
    assert result.groups[0].reason == CatalogDuplicateReason.EXACT


def test_same_sku_merged() -> None:
    a = _entry("id-a", sub="하이브리드", desc="desc one")
    b = _entry("id-b", sub="하이브리드", desc="desc two longer text here")
    result = canonicalize_catalog_entries([a, b])
    assert len(result.entries) == 1
    assert result.groups[0].reason == CatalogDuplicateReason.SAME_SKU


def test_different_subcategory_not_merged() -> None:
    a = _entry("id-a", sub="임베딩 검색", desc="한국어·영어 최적화 임베딩 + 의미 검색")
    b = _entry("id-b", sub="하이브리드 검색", desc="BM25 + 벡터 하이브리드 검색")
    result = canonicalize_catalog_entries([a, b])
    assert len(result.entries) == 2
    assert not result.alias_map


def test_strict_near_merges_copy_paste() -> None:
    long_desc = "동일한 카탈로그 설명 문장입니다. " * 8
    a = _entry("id-a", major="K X", mid="Y", sub="Z", name="Other", desc=long_desc)
    b = _entry("id-b", major="K Q", mid="W", sub="V", name="Other2", desc=long_desc)
    result = canonicalize_catalog_entries([a, b])
    assert len(result.entries) == 1
    assert result.groups[0].reason == CatalogDuplicateReason.STRICT_NEAR


def test_resolve_catalog_id_chains() -> None:
    alias = {"b": "a", "c": "b"}
    assert resolve_catalog_id("c", alias) == "a"
