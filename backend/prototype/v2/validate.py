"""
정합성 검사 — 정답 엑셀의 모든 항목이 시스템 출력에 존재하는지(recall).

핵심: 정답 엑셀의 'detail' 컬럼도 시스템과 **같은 classify() 스키마**로 찾는다.
별도 하드코딩 우선순위 없음. line-by-line 일치는 요구하지 않고(뭉침 허용),
정답 detail 텍스트가 어떤 출력 행에 내용상 포함되면 covered 로 본다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import openpyxl

from .classify import classify
from .extract import Req
from .grid import Grid
from .text import norm, sig

SKIP_SHEETS = ("qna", "rfp 요구사항 →", "요구사항 총괄표")


@dataclass
class GoldItem:
    sheet: str
    text: str


def _header_row(rows) -> int:
    for i, r in enumerate(rows[:6]):
        ne = [c for c in r if c not in (None, "")]
        if len(ne) >= 3:
            return i
    return 0


def _sheet_grid(rows, hi: int) -> Grid:
    cells = [[norm(c) if c is not None else "" for c in r] for r in rows[hi:]]
    width = max((len(r) for r in cells), default=0)
    for r in cells:
        r.extend([""] * (width - len(r)))
    return Grid(cells=cells, table_id=-1, page=None)


def load_gold(xlsx: str) -> list[GoldItem]:
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    gold: list[GoldItem] = []
    for ws in wb.worksheets:
        if ws.title.strip().lower() in SKIP_SHEETS:
            continue
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            continue
        hi = _header_row(rows)
        grid = _sheet_grid(rows, hi)
        roles = classify(grid, header_row=0)
        if "detail" not in roles:
            continue
        det = roles["detail"]
        for r in range(1, grid.nrows):
            val = grid.cells[r][det] if det < len(grid.cells[r]) else ""
            if len(sig(val)) >= 4:
                gold.append(GoldItem(sheet=ws.title, text=val))
    return gold


def _covered(gold_sig: str, out_sigs: list[str], union: str) -> bool:
    """정답 내용이 출력에 존재하는가 — 분할지점 무시(union substring) 우선.

    LLM/규칙이 정답과 다른 지점에서 쪼개도, 내용이 출력 전체에 있으면 covered.
    행(atom)단위가 아니라 분할무시로 측정해야 '정답에 있는 건 다 있나'를 옳게 잰다.
    """
    if len(gold_sig) < 4:
        return True
    if gold_sig in union:        # 분할지점 무시: 출력 전체에 연속 포함
        return True
    grams = {gold_sig[i:i + 6] for i in range(0, max(1, len(gold_sig) - 5))}
    if not grams:
        return False
    for osig in out_sigs:
        if not osig:
            continue
        if sum(1 for gm in grams if gm in osig) / len(grams) >= 0.6:
            return True
    return False


def completeness(gold: list[GoldItem], reqs: list[Req]) -> dict:
    out_sigs = [sig(r.detail) for r in reqs]
    union = "".join(out_sigs)   # 구분자 없이 — 분할지점 무시 매칭
    covered, missing = 0, []
    for g in gold:
        if _covered(sig(g.text), out_sigs, union):
            covered += 1
        else:
            missing.append(g)
    return {
        "gold_total": len(gold),
        "covered": covered,
        "recall": covered / len(gold) if gold else 0.0,
        "missing": missing,
        "missing_by_sheet": dict(Counter(m.sheet for m in missing)),
    }
