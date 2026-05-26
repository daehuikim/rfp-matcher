from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path

from app.phase2.catalog.store import CatalogEntry

logger = logging.getLogger(__name__)

_DEFAULT_NEAR_THRESHOLD = 0.92
_DEFAULT_STRICT_NEAR_THRESHOLD = 0.98


class CatalogDuplicateReason(StrEnum):
    EXACT = "exact"
    SAME_SKU = "same_sku"
    NEAR = "near"
    STRICT_NEAR = "strict_near"


@dataclass(frozen=True)
class CatalogDuplicateGroup:
    canonical_id: str
    duplicate_ids: list[str]
    reason: CatalogDuplicateReason
    similarity: float | None = None


@dataclass
class CatalogCanonicalizationResult:
    entries: list[CatalogEntry]
    alias_map: dict[str, str] = field(default_factory=dict)
    groups: list[CatalogDuplicateGroup] = field(default_factory=list)

    @property
    def removed_count(self) -> int:
        return len(self.alias_map)


def canonicalize_catalog_entries(
    entries: list[CatalogEntry],
    *,
    near_threshold: float = _DEFAULT_NEAR_THRESHOLD,
    strict_near_threshold: float = _DEFAULT_STRICT_NEAR_THRESHOLD,
) -> CatalogCanonicalizationResult:
    """
    카탈로그 항목 dedup·canonicalize.

    1. exact — id 제외 전 필드 동일
    2. same_sku — (솔루션명, 소분류) 동일·id 상이
    3. near — (대·중·소분류) 동일 + embedding_text 유사도 ≥ near_threshold
    4. strict_near — 유사도 ≥ strict_near_threshold (소분류 무관, 카피페이스트)
    """
    if not entries:
        return CatalogCanonicalizationResult(entries=[])

    by_id = {e.id: e for e in entries}
    uf = _UnionFind([e.id for e in entries])
    group_reason: dict[frozenset[str], CatalogDuplicateReason] = {}
    group_sim: dict[frozenset[str], float] = {}

    _union_exact(by_id, uf, group_reason)
    _union_same_sku(by_id, uf, group_reason)
    _union_near(by_id, uf, group_reason, group_sim, near_threshold, strict_near_threshold)

    groups: list[CatalogDuplicateGroup] = []
    alias_map: dict[str, str] = {}
    out_entries: list[CatalogEntry] = []

    for member_ids in uf.groups():
        merged = _merge_group([by_id[i] for i in member_ids])
        out_entries.append(merged)
        if len(member_ids) > 1:
            dup_ids = [i for i in member_ids if i != merged.id]
            reason = _pick_reason(member_ids, group_reason)
            sims_in_group = [
                group_sim.get(frozenset({a, b})) or 0.0
                for a in member_ids
                for b in member_ids
                if a != b
            ]
            sim = max(sims_in_group) if sims_in_group else None
            for dup in dup_ids:
                alias_map[dup] = merged.id
            groups.append(
                CatalogDuplicateGroup(
                    canonical_id=merged.id,
                    duplicate_ids=dup_ids,
                    reason=reason,
                    similarity=sim if sim and sim > 0 else None,
                )
            )

    if groups:
        logger.info(
            "catalog canonicalize: %d → %d entries (%d groups, %d aliases)",
            len(entries),
            len(out_entries),
            len(groups),
            len(alias_map),
        )

    return CatalogCanonicalizationResult(
        entries=out_entries,
        alias_map=alias_map,
        groups=groups,
    )


def resolve_catalog_id(catalog_id: str, alias_map: dict[str, str]) -> str:
    """중복 id → canonical id (체인 따라감)."""
    seen: set[str] = set()
    cur = catalog_id
    while cur in alias_map and cur not in seen:
        seen.add(cur)
        cur = alias_map[cur]
    return cur


def content_fingerprint(entry: CatalogEntry) -> str:
    payload = entry.model_dump()
    payload.pop("id", None)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def embedding_text_similarity(a: CatalogEntry, b: CatalogEntry) -> float:
    ta = _normalize_text(a.embedding_text)
    tb = _normalize_text(b.embedding_text)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    return SequenceMatcher(None, ta, tb).ratio()


def description_similarity(a: CatalogEntry, b: CatalogEntry) -> float:
    ta = _normalize_text(a.설명)
    tb = _normalize_text(b.설명)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    return SequenceMatcher(None, ta, tb).ratio()


def token_jaccard(a: CatalogEntry, b: CatalogEntry) -> float:
    ta = set(_tokenize(a.embedding_text))
    tb = set(_tokenize(b.embedding_text))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def save_catalog_aliases(path: Path, result: CatalogCanonicalizationResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "alias_map": result.alias_map,
        "groups": [
            {
                "canonical_id": g.canonical_id,
                "duplicate_ids": g.duplicate_ids,
                "reason": g.reason.value,
                "similarity": g.similarity,
            }
            for g in result.groups
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_catalog_aliases(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data.get("alias_map") or {})
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("catalog alias 로드 실패 path=%s: %s", path, e)
        return {}


class _UnionFind:
    def __init__(self, ids: list[str]) -> None:
        self._parent = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def groups(self) -> list[list[str]]:
        buckets: dict[str, list[str]] = {}
        for node in self._parent:
            root = self.find(node)
            buckets.setdefault(root, []).append(node)
        return list(buckets.values())


def _set_group_reason(
    reasons: dict[frozenset[str], CatalogDuplicateReason],
    a: str,
    b: str,
    reason: CatalogDuplicateReason,
) -> None:
    key = frozenset({a, b})
    current = reasons.get(key)
    if current is None or _reason_priority(reason) < _reason_priority(current):
        reasons[key] = reason


def _reason_priority(reason: CatalogDuplicateReason) -> int:
    order = [
        CatalogDuplicateReason.EXACT,
        CatalogDuplicateReason.SAME_SKU,
        CatalogDuplicateReason.STRICT_NEAR,
        CatalogDuplicateReason.NEAR,
    ]
    return order.index(reason)


def _union_exact(
    by_id: dict[str, CatalogEntry],
    uf: _UnionFind,
    reasons: dict[frozenset[str], CatalogDuplicateReason],
) -> None:
    by_fp: dict[str, list[str]] = {}
    for eid, entry in by_id.items():
        by_fp.setdefault(content_fingerprint(entry), []).append(eid)
    for ids in by_fp.values():
        if len(ids) < 2:
            continue
        base = ids[0]
        for other in ids[1:]:
            uf.union(base, other)
            _set_group_reason(reasons, base, other, CatalogDuplicateReason.EXACT)


def _union_same_sku(
    by_id: dict[str, CatalogEntry],
    uf: _UnionFind,
    reasons: dict[frozenset[str], CatalogDuplicateReason],
) -> None:
    by_sku: dict[tuple[str, str], list[str]] = {}
    for eid, entry in by_id.items():
        key = (_normalize_text(entry.솔루션명), _normalize_text(entry.소분류))
        by_sku.setdefault(key, []).append(eid)
    for ids in by_sku.values():
        if len(ids) < 2:
            continue
        base = ids[0]
        for other in ids[1:]:
            uf.union(base, other)
            _set_group_reason(reasons, base, other, CatalogDuplicateReason.SAME_SKU)


def _union_near(
    by_id: dict[str, CatalogEntry],
    uf: _UnionFind,
    reasons: dict[frozenset[str], CatalogDuplicateReason],
    sims: dict[frozenset[str], float],
    near_threshold: float,
    strict_near_threshold: float,
) -> None:
    ids = list(by_id.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = by_id[ids[i]], by_id[ids[j]]
            full_sim = embedding_text_similarity(a, b)
            desc_sim = description_similarity(a, b)
            key = frozenset({a.id, b.id})
            if desc_sim >= strict_near_threshold and len(a.설명.strip()) >= 12:
                uf.union(a.id, b.id)
                _set_group_reason(reasons, a.id, b.id, CatalogDuplicateReason.STRICT_NEAR)
                sims[key] = desc_sim
                continue
            same_taxonomy = (
                _normalize_text(a.대분류) == _normalize_text(b.대분류)
                and _normalize_text(a.중분류) == _normalize_text(b.중분류)
                and _normalize_text(a.소분류) == _normalize_text(b.소분류)
            )
            if same_taxonomy and full_sim >= near_threshold:
                uf.union(a.id, b.id)
                _set_group_reason(reasons, a.id, b.id, CatalogDuplicateReason.NEAR)
                sims[key] = full_sim


def _merge_group(entries: list[CatalogEntry]) -> CatalogEntry:
    entries = sorted(entries, key=lambda e: (len(e.embedding_text), e.id), reverse=True)
    base = entries[0].model_copy()
    if len(entries) == 1:
        return base

    canonical = sorted(entries, key=lambda e: e.id)[0]
    merged = canonical.model_copy()
    if len(base.설명) > len(merged.설명):
        merged.설명 = base.설명

    def _union_list(field: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for e in entries:
            for item in getattr(e, field):
                norm = item.strip()
                if norm and norm not in seen:
                    seen.add(norm)
                    out.append(norm)
        return out

    merged.강점 = _union_list("강점")
    merged.한계 = _union_list("한계")
    merged.레퍼런스 = _union_list("레퍼런스")
    return merged


def _pick_reason(
    member_ids: list[str],
    reasons: dict[frozenset[str], CatalogDuplicateReason],
) -> CatalogDuplicateReason:
    priority = [
        CatalogDuplicateReason.EXACT,
        CatalogDuplicateReason.SAME_SKU,
        CatalogDuplicateReason.STRICT_NEAR,
        CatalogDuplicateReason.NEAR,
    ]
    found: set[CatalogDuplicateReason] = set()
    for i, a in enumerate(member_ids):
        for b in member_ids[i + 1 :]:
            r = reasons.get(frozenset({a, b}))
            if r:
                found.add(r)
    for p in priority:
        if p in found:
            return p
    return CatalogDuplicateReason.NEAR


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\w가-힣]{2,}", text.lower())
