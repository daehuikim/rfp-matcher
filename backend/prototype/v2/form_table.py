"""
세로형(form / key-value) 표 처리 — 요구사항 1건이 '필드:값' 행 블록으로 세로 배열된 표.

법제처처럼 [요구사항 분류 | …], [요구사항 고유번호 | SFR-001], [요구사항 명칭 | …],
[요구사항 상세설명 | 정의 | …], [요구사항 상세설명 | 세부내용 | …] 형태.

감지·매핑은 **기존 역할 스키마(schema.REQUIREMENT_SCHEMA)** 로만 — 라벨 컬럼이
역할 어휘(분류/고유번호/명칭/상세/세부 …)로 채워졌는지로 판단(하드코딩 아님).
고유번호(id) 등장마다 새 블록. 탭은 고유번호 접두사(SFR/DAR …).
"""
from __future__ import annotations

import re

from .extract import Req, atomize_detail
from .grid import Grid
from .schema import REQUIREMENT_SCHEMA
from .text import norm, sig

_ID_PREFIX = re.compile(r"^([A-Za-z]{2,4})[-_ ]?\d")


def _label_role(label: str) -> str | None:
    ls = sig(label).lower()
    raw = norm(label).lower()
    if not ls:
        return None
    for spec in REQUIREMENT_SCHEMA:
        for t in spec.header_terms:
            ts = sig(t).lower()
            if ts and (ts in ls or t in raw):
                return spec.name
    return None


def detect_form_label_col(grid: Grid) -> int | None:
    """라벨 컬럼(반복되는 역할 라벨들)이 있으면 그 인덱스, 없으면 None."""
    if grid.ncols < 2 or grid.nrows < 6:
        return None
    for col in (0, 1):
        vals = [grid.cells[r][col] for r in range(grid.nrows) if grid.cells[r][col]]
        if len(vals) < 6:
            continue
        role_rows = sum(1 for v in vals if _label_role(v))
        distinct_roles = {_label_role(v) for v in vals if _label_role(v)}
        # col0 의 상당수가 역할 라벨이고(반복), 역할 종류 3가지 이상이면 세로형
        # (하위표 등 비라벨 행이 섞여 있어도 비율로 판정 — pivot 은 비라벨 무시)
        if len(distinct_roles) >= 3 and role_rows >= 6 and role_rows / len(vals) >= 0.4:
            return col
    return None


def _value(grid: Grid, r: int, label_col: int) -> tuple[str, str]:
    """(sub_label, value) — label_col 다음이 sub(정의/세부) 또는 값."""
    sub = grid.cells[r][label_col + 1] if grid.ncols > label_col + 1 else ""
    value = ""
    for c in range(label_col + 2, grid.ncols):
        if grid.cells[r][c]:
            value = grid.cells[r][c]
            break
    if not value and sub and _label_role(sub) is None:
        # 2열 폼: [라벨 | 값]
        value, sub = sub, ""
    return sub, value


def pivot_form_table(doc_name: str, grid: Grid, label_col: int, mode: str) -> list[Req]:
    blocks: list[dict] = []
    cur: dict = {}
    for r in range(grid.nrows):
        label = grid.cells[r][label_col]
        if not label:
            continue
        role = _label_role(label)
        if role is None:
            continue
        sub, value = _value(grid, r, label_col)
        if role == "id":
            if cur.get("id"):
                blocks.append(cur)
            cur = {"id": value}
        elif role == "detail":
            # 정의 / 세부내용 구분
            if "정의" in sub:
                cur["def"] = value
            else:
                cur["detail"] = (cur.get("detail", "") + " " + value).strip() if cur.get("detail") else value
        elif role in ("item", "category"):
            cur.setdefault(role, value)
    if cur.get("id"):
        blocks.append(cur)

    out: list[Req] = []
    for b in blocks:
        rid = b.get("id", "")
        m = _ID_PREFIX.match(rid)
        tab = m.group(1).upper() if m else (b.get("category") or "요구사항")
        top = b.get("item", "")          # 명칭 → 항목명
        mid = b.get("def", "")           # 정의 → 요구사항
        detail = b.get("detail") or b.get("def") or ""
        if len(sig(detail)) < 2:
            continue
        # 세로형 표는 요구사항 1건 = 1행(세부내용 통째). 입도 분할하지 않음.
        src = f"표#{grid.table_id} · {rid}" if rid else f"표#{grid.table_id}"
        r = Req(doc=doc_name, table_id=grid.table_id, page=grid.page,
                rid=rid, top=norm(top), mid=norm(mid), detail=detail, source=src)
        r.tab = tab
        out.append(r)
    return out
