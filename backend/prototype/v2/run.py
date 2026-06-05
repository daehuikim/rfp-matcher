"""
CLI 오케스트레이션 — 변환은 외부(opendataloader)에서 끝났다고 가정하고 JSON 입력.

usage: python -m prototype.v2.run <odl.json> [gold.xlsx] [out.xlsx]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl

from .extract import Req, extract_grids
from .grid import grids_from_opendataloader
from .validate import completeness, load_gold


def write_excel(reqs: list[Req], out_path: str) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "요구사항"
    ws.append(["ID", "분류", "항목명", "상세요건", "page", "table_id"])
    for r in reqs:
        ws.append([r.rid, r.category, r.item, r.detail, r.page, r.table_id])
    wb.save(out_path)


def main() -> None:
    json_path = Path(sys.argv[1])
    gold_xlsx = sys.argv[2] if len(sys.argv) > 2 else None
    out_xlsx = sys.argv[3] if len(sys.argv) > 3 else "/tmp/v2_out.xlsx"

    doc = json.loads(json_path.read_text(encoding="utf-8"))
    grids = grids_from_opendataloader(doc)
    reqs = extract_grids(doc.get("file name", json_path.stem), grids)
    write_excel(reqs, out_xlsx)
    print(f"표 {len(grids)}개 → 추출 행 {len(reqs)}  → {out_xlsx}")

    if gold_xlsx:
        gold = load_gold(gold_xlsx)
        res = completeness(gold, reqs)
        print(f"정답 항목 {res['gold_total']} | 커버 {res['covered']} | "
              f"recall {res['recall']*100:.1f}% | 누락 {len(res['missing'])}")
        print("누락 시트별:", res["missing_by_sheet"])
        for m in res["missing"][:12]:
            print(f"  - [{m.sheet}] {m.text[:72]}")


if __name__ == "__main__":
    main()
