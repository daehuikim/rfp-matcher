#!/usr/bin/env python3
"""
KT 카탈로그 dedup·canonicalize — exact / same-SKU / near-duplicate 탐지.

사용:
  cd backend && PYTHONPATH=. python scripts/canonicalize_catalog.py
  PYTHONPATH=. python scripts/canonicalize_catalog.py --write
  PYTHONPATH=. python scripts/canonicalize_catalog.py --report ../data/catalog/dedup_report.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.phase2.catalog.canonicalizer import (  # noqa: E402
    canonicalize_catalog_entries,
    embedding_text_similarity,
    save_catalog_aliases,
    token_jaccard,
)
from app.phase2.catalog.store import CatalogStore  # noqa: E402


def _fmt_group(g, entries_by_id) -> str:
    canon = entries_by_id[g.canonical_id]
    lines = [
        f"### `{g.canonical_id}` ← {g.reason.value}",
        f"- canonical: **{canon.솔루션명}** · {canon.소분류}",
    ]
    if g.similarity is not None:
        lines.append(f"- max similarity: **{g.similarity:.3f}**")
    lines.append("- merged ids:")
    for dup in g.duplicate_ids:
        d = entries_by_id[dup]
        sim = embedding_text_similarity(canon, d)
        jacc = token_jaccard(canon, d)
        lines.append(
            f"  - `{dup}` ({d.소분류}) — ratio={sim:.3f}, jaccard={jacc:.3f}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="카탈로그 canonicalize / dedup")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="입력 JSON (기본: settings.catalog_path)",
    )
    parser.add_argument("--write", action="store_true", help="canonical JSON·alias 저장")
    parser.add_argument("--report", type=Path, default=None, help="Markdown 리포트 경로")
    parser.add_argument("--near", type=float, default=0.92, help="near-duplicate threshold")
    args = parser.parse_args()

    settings = get_settings()
    in_path = args.input or settings.catalog_path
    store = CatalogStore.load(in_path)
    entries = store.entries
    by_id = {e.id: e for e in entries}

    result = canonicalize_catalog_entries(entries, near_threshold=args.near)

    print(f"입력: {len(entries)} entries @ {in_path}")
    print(f"출력: {len(result.entries)} entries (removed {result.removed_count})")
    print(f"duplicate groups: {len(result.groups)}")

    if result.groups:
        print("\n--- duplicate groups ---")
        for g in result.groups:
            print(f"  [{g.reason.value}] {g.canonical_id} ← {g.duplicate_ids}")

    # same 솔루션명 (정보용 — SKU는 유지)
    from collections import defaultdict

    by_name: dict[str, list] = defaultdict(list)
    for e in result.entries:
        by_name[e.솔루션명].append(e.소분류)
    multi = {k: v for k, v in by_name.items() if len(v) > 1}
    if multi:
        print(f"\n동일 솔루션명·다른 소분류 (의도적 SKU, {len(multi)} brands):")
        for name, subs in sorted(multi.items(), key=lambda x: -len(x[1])):
            print(f"  {name}: {', '.join(subs)}")

    if args.report:
        report_lines = [
            "# Catalog dedup report",
            "",
            f"| 항목 | 값 |",
            f"|------|-----|",
            f"| 입력 | {len(entries)} |",
            f"| 출력 | {len(result.entries)} |",
            f"| 제거 | {result.removed_count} |",
            f"| 그룹 | {len(result.groups)} |",
            "",
        ]
        if result.groups:
            report_lines.append("## Merged groups")
            report_lines.append("")
            for g in result.groups:
                report_lines.append(_fmt_group(g, by_id))
                report_lines.append("")
        else:
            report_lines.append("_exact/near duplicate 없음 — SKU 중복(다른 소분류)만 존재할 수 있음._")
            report_lines.append("")
            report_lines.append("## 동일 솔루션명 · 다른 소분류 (SKU)")
            for name, subs in sorted(multi.items(), key=lambda x: -len(x[1])):
                report_lines.append(f"- **{name}**: {', '.join(subs)}")

        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("\n".join(report_lines), encoding="utf-8")
        print(f"\n리포트: {args.report}")

    if args.write:
        out_path = settings.catalog_path
        backup = out_path.with_suffix(".json.bak")
        backup.write_text(in_path.read_text(encoding="utf-8"), encoding="utf-8")
        store.replace(result.entries)
        store.save()
        save_catalog_aliases(settings.catalog_aliases_path, result)
        # bm25_index 동기화
        bm25_path = settings.catalog_bm25_path / "catalog_entries.json"
        if bm25_path.parent.is_dir():
            bm25_path.write_text(
                json.dumps([e.model_dump() for e in result.entries], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(f"\n저장: {out_path} (backup: {backup})")
        print(f"aliases: {settings.catalog_aliases_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
