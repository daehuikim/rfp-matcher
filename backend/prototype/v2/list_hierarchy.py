"""
한국 RFP 리스트/불릿 계위 — 가·나·1)·- 를 항목명·요구사항·상세요건으로 결정적 매핑.
"""
from __future__ import annotations

import re

from .extract import Req
from .hierarchy_collapse import _to_bullet
from .text import norm, sig

_HANGUL_TOP = re.compile(r"^\s*([가-힣])\.\s*(.+)$")
_NUM_MID = re.compile(r"^\s*(\d+)\)\s*(.*)$")
_BULLET = re.compile(r"^\s*[-∙•·–—]\s*(.+)$")
_FOOT = re.compile(r"^\s*(\*\d+.*)$")
_BUILD_PAREN = re.compile(r"^(.+?)\s*구축\s*(\([^)]+\))\s*$")


def _normalize_top(label: str) -> str:
    """'지식베이스 구축(RAG)' → '지식베이스(RAG)' (정답 조견표 항목명)."""
    m = _BUILD_PAREN.match(label.strip())
    if m:
        return f"{m.group(1).strip()}{m.group(2)}"
    return label.strip()


def _is_list_source(r: Req) -> bool:
    return r.table_id < 0 or "리스트" in (r.source or "")


def _parse_line(text: str) -> tuple[str, str, str]:
    t = norm(text)
    if not t:
        return "plain", "", ""
    m = _HANGUL_TOP.match(t)
    if m:
        return "top", _normalize_top(m.group(2).strip()), ""
    m = _NUM_MID.match(t)
    if m:
        body = m.group(2).strip()
        short = re.split(r"[,，.。\n]", body, maxsplit=1)[0].strip()
        mid = short[:48] if short else body[:48]
        return "mid", mid, body
    m = _BULLET.match(t)
    if m:
        return "detail", "", m.group(1).strip()
    m = _FOOT.match(t)
    if m:
        return "detail", "", m.group(1).strip()
    return "plain", "", t


def _bucket_key(r: Req) -> tuple:
    return (r.tab or "", r.table_id, r.page if r.page is not None else -1)


def refine_list_hierarchy(reqs: list[Req]) -> tuple[list[Req], int]:
    """리스트 유래 행의 가·나·1)·- 계위를 결정적으로 재배치."""
    out: list[Req] = []
    changed = 0
    cur_bucket: tuple | None = None
    cur_top = ""
    cur_mid = ""

    for r in reqs:
        if not _is_list_source(r):
            out.append(r)
            cur_bucket = None
            cur_top = cur_mid = ""
            continue

        k = _bucket_key(r)
        if k != cur_bucket:
            cur_bucket = k
            cur_top = cur_mid = ""

        kind, label, body = _parse_line(r.detail)
        if kind == "top":
            if cur_top != label:
                changed += 1
            cur_top, cur_mid = label, ""
            continue

        if kind == "mid":
            body = body or label
            bullet = _to_bullet(body) if body else f"- {label}"
            cur_mid = label
            nr = Req(
                doc=r.doc,
                table_id=r.table_id,
                page=r.page,
                top=cur_top,
                mid=cur_mid,
                detail=bullet,
                section_path=r.section_path,
                tab=r.tab,
                source=r.source,
            )
            if nr.top != r.top or nr.mid != r.mid or nr.detail != r.detail:
                changed += 1
            out.append(nr)
            continue

        detail = body or r.detail
        if len(sig(detail)) < 2:
            continue
        bullet = _to_bullet(detail) if kind in ("detail", "plain") else detail
        # bullet 연속행은 top/mid 비움 — anchor 는 1) 행·LLM 이 담당
        nr = Req(
            doc=r.doc,
            table_id=r.table_id,
            page=r.page,
            top="",
            mid="",
            detail=bullet,
            section_path=r.section_path,
            tab=r.tab,
            source=r.source,
        )
        if nr.detail != r.detail:
            changed += 1
        out.append(nr)

    return out, changed
