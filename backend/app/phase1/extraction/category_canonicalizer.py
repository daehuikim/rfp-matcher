from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.phase1.extraction.table_columns import is_valid_category_label

logger = logging.getLogger(__name__)

# 기본 동의어 — data/taxonomy/categories.json 으로 확장·덮어쓰기 가능
_DEFAULT_ALIASES: dict[str, str] = {
    "요건구분": "요건 구분",
    "요건 구분": "요건 구분",
    "구분": "구분",
    "상세내용": "상세내용",
    "상세 내용": "상세내용",
    "데이터수집": "데이터 수집",
    "데이터 수집": "데이터 수집",
    "저장구조": "저장 구조",
    "저장 구조": "저장 구조",
    "저장 구조 및 데이터 계층 관리": "저장 구조",
    "저장 구조 및": "저장 구조",
    "임베딩 및 벡터 검색": "임베딩·벡터 검색",
    "임베딩·벡터 검색": "임베딩·벡터 검색",
    "검색 및 포털": "검색·포털",
    "검색·포털": "검색·포털",
}

_DEFAULT_TRUNCATIONS: dict[str, str] = {
    "니터링": "모니터링",
    "모니터": "모니터링",
}


class CanonicalizationStep(StrEnum):
    """파이프라인에서 적용되는 정규화 단계 (순서 고정)."""

    NORMALIZE = "normalize"
    ALIAS = "alias"
    TRUNCATION = "truncation"
    SUBSTRING_MERGE = "substring_merge"
    SINGLETON_ABSORB = "singleton_absorb"
    TAXONOMY = "taxonomy"


@dataclass(frozen=True)
class CategoryCanonicalizationResult:
    """문서 단위 분류 canonicalization 결과."""

    labels: list[str]
    raw_distinct: int
    canonical_distinct: int
    merge_map: dict[str, str] = field(default_factory=dict)
    steps_applied: list[str] = field(default_factory=list)


class CategoryCanonicalizer:
    """
    추출된 분류 라벨을 문서 단위로 canonicalize.

    Classifier는 category_raw만 넘기고, 별칭·절단·병합·taxonomy는 이 스텝에서 수행.
    """

    def __init__(
        self,
        *,
        aliases: dict[str, str] | None = None,
        truncations: dict[str, str] | None = None,
        coarse_map: dict[str, str] | None = None,
        taxonomy_path: Path | None = None,
    ) -> None:
        self._aliases = dict(_DEFAULT_ALIASES)
        self._truncations = dict(_DEFAULT_TRUNCATIONS)
        self._coarse_map: dict[str, str] = {}
        if aliases:
            self._aliases.update(aliases)
        if truncations:
            self._truncations.update(truncations)
        if coarse_map:
            self._coarse_map.update(coarse_map)
        if taxonomy_path and taxonomy_path.is_file():
            self._load_taxonomy_file(taxonomy_path)

    def canonicalize(self, raw_labels: list[str]) -> CategoryCanonicalizationResult:
        if not raw_labels:
            return CategoryCanonicalizationResult(
                labels=[],
                raw_distinct=0,
                canonical_distinct=0,
                steps_applied=[],
            )

        steps: list[str] = [CanonicalizationStep.NORMALIZE.value]
        merge_map: dict[str, str] = {}

        normalized = [_normalize_whitespace(x) for x in raw_labels]
        normalized = [_coerce_category_label(x) for x in normalized]
        raw_distinct = len({x or "기타" for x in normalized})

        aliased: list[str] = []
        for raw in normalized:
            before = raw or "기타"
            after = self._apply_alias(before)
            if after != before:
                steps.append(CanonicalizationStep.ALIAS.value)
                merge_map[before] = after
            aliased.append(after)

        fixed: list[str] = []
        for lab in aliased:
            before = lab
            after = self._apply_truncation(lab)
            if after != before:
                steps.append(CanonicalizationStep.TRUNCATION.value)
                merge_map[before] = after
            fixed.append(after)

        merged, substring_map = _merge_substring_labels(fixed)
        if substring_map:
            steps.append(CanonicalizationStep.SUBSTRING_MERGE.value)
            merge_map.update(substring_map)

        absorbed, singleton_map = _absorb_singleton_labels(merged)
        if singleton_map:
            steps.append(CanonicalizationStep.SINGLETON_ABSORB.value)
            merge_map.update(singleton_map)

        final = self._apply_coarse_taxonomy(absorbed)
        if any(absorbed[i] != final[i] for i in range(len(absorbed))):
            steps.append(CanonicalizationStep.TAXONOMY.value)
            for before, after in zip(absorbed, final, strict=True):
                if before != after:
                    merge_map[before] = after

        unique_steps = _dedupe_preserve_order(steps)
        canonical_distinct = len(set(final))

        if canonical_distinct < raw_distinct:
            logger.info(
                "category canonicalize: %d → %d distinct (steps=%s)",
                raw_distinct,
                canonical_distinct,
                unique_steps,
            )

        return CategoryCanonicalizationResult(
            labels=final,
            raw_distinct=raw_distinct,
            canonical_distinct=canonical_distinct,
            merge_map=merge_map,
            steps_applied=unique_steps,
        )

    def _load_taxonomy_file(self, path: Path) -> None:
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("taxonomy 파일 로드 실패 path=%s: %s", path, e)
            return
        self._aliases.update(data.get("aliases") or {})
        self._truncations.update(data.get("truncations") or {})
        self._coarse_map.update(data.get("coarse_map") or {})
        logger.info(
            "taxonomy 로드: aliases=%d truncations=%d coarse=%d",
            len(data.get("aliases") or {}),
            len(data.get("truncations") or {}),
            len(data.get("coarse_map") or {}),
        )

    def _apply_alias(self, label: str) -> str:
        if label in self._aliases:
            return self._aliases[label]
        compact = re.sub(r"\s+", "", label)
        return self._aliases.get(compact, label)

    def _apply_truncation(self, label: str) -> str:
        if label in self._truncations:
            return self._truncations[label]
        compact = re.sub(r"\s+", "", label)
        return self._truncations.get(compact, label)

    def _apply_coarse_taxonomy(self, labels: list[str]) -> list[str]:
        if not self._coarse_map:
            return labels
        return [self._coarse_map.get(lab, lab) for lab in labels]


def build_category_canonicalizer(taxonomy_path: Path | None = None) -> CategoryCanonicalizer:
    return CategoryCanonicalizer(taxonomy_path=taxonomy_path)


def _normalize_whitespace(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.split())


def _coerce_category_label(label: str) -> str:
    if not label:
        return "기타"
    if is_valid_category_label(label):
        return label
    return "기타"


def _merge_substring_labels(labels: list[str]) -> tuple[list[str], dict[str, str]]:
    unique = sorted(
        {x for x in labels if x != "기타" and is_valid_category_label(x)},
        key=len,
        reverse=True,
    )
    merge_map: dict[str, str] = {}
    for long in unique:
        for short in unique:
            if long == short or len(short) >= len(long):
                continue
            if short in long:
                merge_map[short] = long
    merged = [merge_map.get(x, x) for x in labels]
    return merged, merge_map


def _absorb_singleton_labels(labels: list[str]) -> tuple[list[str], dict[str, str]]:
    counts = Counter(labels)
    dominant = [c for c, n in counts.items() if n >= 2 and c != "기타" and is_valid_category_label(c)]
    if not dominant:
        return labels, {}

    merge_map: dict[str, str] = {}
    out: list[str] = []
    for lab in labels:
        if lab == "기타" or counts[lab] >= 2:
            out.append(lab)
            continue
        best = _nearest_dominant(lab, dominant)
        target = best or "기타"
        if target != lab:
            merge_map[lab] = target
        out.append(target)
    return out, merge_map


def _nearest_dominant(label: str, dominant: list[str]) -> str | None:
    best: str | None = None
    best_score = 0.0
    for dom in dominant:
        score = _label_similarity(label, dom)
        if score > best_score:
            best_score = score
            best = dom
    return best if best_score >= 0.55 else None


def _label_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9
    ca = re.sub(r"\s+", "", a)
    cb = re.sub(r"\s+", "", b)
    prefix = 0
    for x, y in zip(ca, cb, strict=False):
        if x != y:
            break
        prefix += 1
    if prefix >= 3:
        return prefix / max(len(ca), len(cb))
    return 0.0


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def category_histogram(categories: list[str]) -> dict[str, int]:
    return dict(Counter(categories))
