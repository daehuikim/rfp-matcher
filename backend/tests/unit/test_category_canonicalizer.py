from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.phase1.extraction.category_canonicalizer import (
    CategoryCanonicalizer,
    CanonicalizationStep,
    build_category_canonicalizer,
)


def test_normalize_alias_and_truncation() -> None:
    canon = CategoryCanonicalizer()
    result = canon.canonicalize(["데이터수집", "니터링", ""])
    assert result.labels == ["데이터 수집", "모니터링", "기타"]
    assert CanonicalizationStep.ALIAS.value in result.steps_applied
    assert CanonicalizationStep.TRUNCATION.value in result.steps_applied


def test_substring_merge() -> None:
    canon = CategoryCanonicalizer()
    labels = ["메타데이터 / 리니지 / 버전 관리", "리니지", "메타데이터 / 리니지 / 버전 관리"]
    result = canon.canonicalize(labels)
    assert result.labels == ["메타데이터 / 리니지 / 버전 관리"] * 3
    assert CanonicalizationStep.SUBSTRING_MERGE.value in result.steps_applied


def test_singleton_absorb() -> None:
    canon = CategoryCanonicalizer()
    labels = ["데이터 수집"] * 3 + ["데이터수집"]
    result = canon.canonicalize(labels)
    assert all(x == "데이터 수집" for x in result.labels)


def test_coarse_taxonomy_from_file(tmp_path: Path) -> None:
    tax = tmp_path / "categories.json"
    tax.write_text(
        json.dumps(
            {
                "coarse_map": {
                    "임베딩·벡터 검색": "검색·인프라",
                    "검색·포털": "검색·인프라",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    canon = build_category_canonicalizer(tax)
    result = canon.canonicalize(["임베딩 및 벡터 검색", "검색 및 포털"])
    assert result.labels == ["검색·인프라", "검색·인프라"]
    assert CanonicalizationStep.TAXONOMY.value in result.steps_applied


def test_merge_map_records_changes() -> None:
    canon = CategoryCanonicalizer()
    result = canon.canonicalize(["니터링", "니터링"])
    assert result.merge_map.get("니터링") == "모니터링"
    assert result.raw_distinct == 1
    assert result.canonical_distinct == 1


def test_invalid_long_labels_coerced_to_etc() -> None:
    canon = CategoryCanonicalizer()
    blob = "비정형 데이터 플랫폼 구축 " + "측정 ·관리할 수 있어야 합니다. " * 3
    result = canon.canonicalize(["데이터 수집", blob, blob])
    assert result.labels[0] == "데이터 수집"
    assert result.labels[1] == "기타"
    assert result.labels[2] == "기타"
