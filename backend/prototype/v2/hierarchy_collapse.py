"""
조견표 계위 후처리 — 정답 엑셀 스타일(항목명·요구사항·- bullet)로 묶기.

- top 에 'X / Y' 병합 → top=X, mid=Y 분리
- 1) 리스트 → 추론 mid + '- ' bullet detail
- 연속 bullet 은 첫 행만 top/mid, 이후 carry(빈 칸)
"""
from __future__ import annotations

import re

from .extract import Req
from .text import norm, sig

_BULLET = re.compile(r"^\s*[-∙•·–—]\s*")
_NUM_MID = re.compile(r"^\s*\d+\)\s*")
_SCOPE_TAB = re.compile(r"구축방향|BMT|실증|사업.?범위|제안.?요청.?범위", re.I)

def _is_list_source(r: Req) -> bool:
    return r.table_id < 0 or "리스트" in (r.source or "")


def _is_bullet_detail(detail: str) -> bool:
    return bool(_BULLET.match(detail or ""))


def _to_bullet(detail: str) -> str:
    d = norm(detail)
    if _BULLET.match(d):
        return d if d.startswith("-") else re.sub(r"^\s*[-∙•·–—]\s*", "- ", d)
    m = _NUM_MID.match(d)
    if m:
        body = _NUM_MID.sub("", d).strip()
        return f"- {body}"
    return f"- {d}" if d else d


def split_merged_top_mid(r: Req) -> bool:
    """top='A / B' → top=A, mid=B. 변경 시 True."""
    top = (r.top or "").strip()
    if " / " not in top:
        return False
    a, b = top.split(" / ", 1)
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    r.top = a
    if not (r.mid or "").strip():
        r.mid = b
    return True


def collapse_bullet_groups(reqs: list[Req]) -> tuple[list[Req], int]:
    """탭·top 기준 bullet 그룹 — anchor 행만 top/mid, 나머지 detail 만."""
    changed = 0
    cur_tab = ""
    anchor_top = ""
    last_mid = ""

    for r in reqs:
        if not _is_list_source(r):
            continue
        if split_merged_top_mid(r):
            changed += 1

        if r.tab != cur_tab:
            cur_tab = r.tab or ""
            anchor_top = last_mid = ""

        detail = norm(r.detail)
        if len(sig(detail)) < 2:
            continue

        is_bullet = _is_bullet_detail(detail)
        is_numbered = bool(_NUM_MID.match(detail)) and _is_list_source(r)

        if _is_bullet_detail(detail) or is_numbered:
            bullet = _to_bullet(detail)
            mid = (r.mid or "").strip()
            top = (r.top or "").strip() or anchor_top

            if top:
                anchor_top = top

            if mid and mid != last_mid:
                r.top = anchor_top
                r.mid = mid
                r.detail = bullet
                last_mid = mid
                changed += 1
            else:
                if r.top or r.mid:
                    changed += 1
                r.top = ""
                r.mid = ""
                r.detail = bullet
        elif _is_list_source(r) and len(sig(detail)) >= 8:
            # scope 탭 plain 1) 본문(접두 제거됨) → bullet + 추론 mid
            bullet = _to_bullet(detail)
            mid = (r.mid or "").strip()
            top = (r.top or "").strip() or anchor_top
            if top:
                anchor_top = top
            if mid and mid != last_mid:
                r.top = top
                r.mid = mid
                r.detail = bullet
                last_mid = mid
                changed += 1
            else:
                if r.top or r.mid:
                    changed += 1
                r.top = ""
                r.mid = ""
                r.detail = bullet
        else:
            if r.top:
                anchor_top = r.top.strip()
            if r.mid:
                last_mid = r.mid.strip()

    return reqs, changed


def promote_table_mid_anchors(reqs: list[Req]) -> tuple[list[Req], int]:
    """표 추출: mid anchor 이후 연속 `-` bullet 은 top/mid 빈칸."""
    changed = 0
    cur_key: tuple | None = None
    anchor_top = anchor_mid = ""

    for r in reqs:
        if _is_list_source(r):
            cur_key = None
            anchor_top = anchor_mid = ""
            continue
        key = (r.tab, r.table_id)
        if key != cur_key:
            cur_key = key
            anchor_top = (r.top or "").strip()
            anchor_mid = ""

        detail = norm(r.detail)
        if len(sig(detail)) < 2:
            continue

        if not _is_bullet_detail(detail) and not _NUM_MID.match(detail):
            if (r.mid or "").strip():
                anchor_mid = r.mid.strip()
            if (r.top or "").strip():
                anchor_top = r.top.strip()
            continue

        bullet = _to_bullet(detail)
        split_merged_top_mid(r)
        mid = (r.mid or "").strip() or anchor_mid
        top = (r.top or "").strip() or anchor_top

        if anchor_mid and mid == anchor_mid:
            if r.top or r.mid:
                changed += 1
            r.top = ""
            r.mid = ""
            r.detail = bullet
        else:
            anchor_mid = mid
            anchor_top = top or anchor_top
            r.top = anchor_top
            r.mid = anchor_mid
            r.detail = bullet
            changed += 1

    return reqs, changed


def dedupe_scope_against_bullets(reqs: list[Req]) -> tuple[list[Req], int]:
    """scope 탭 1) 요약 행 중 상세 탭 bullet 으로 이미 포함된 것 제거(recall union 유지)."""
    detail_sigs: list[str] = []
    for r in reqs:
        if _SCOPE_TAB.search(r.tab or ""):
            continue
        if _is_bullet_detail(r.detail) or not _is_list_source(r):
            detail_sigs.append(sig(r.detail))
    union = "".join(detail_sigs)

    kept: list[Req] = []
    dropped = 0
    for r in reqs:
        if not _SCOPE_TAB.search(r.tab or ""):
            kept.append(r)
            continue
        s = sig(r.detail)
        if len(s) < 12:
            kept.append(r)
            continue
        # numbered list in scope — drop if substantially covered by detail bullets
        if _NUM_MID.match(r.detail) or not _is_bullet_detail(r.detail):
            body = _NUM_MID.sub("", r.detail).strip() if _NUM_MID.match(r.detail) else r.detail
            grams = {body[i:i + 8] for i in range(max(0, len(body) - 7))}
            if grams and sum(1 for g in grams if g in union) / len(grams) >= 0.55:
                dropped += 1
                continue
        kept.append(r)
    return kept, dropped


def enforce_gold_spacing(reqs: list[Req]) -> tuple[list[Req], int]:
    """정답 조견표: 서브그룹 anchor 이후 연속 bullet 은 top/mid 빈칸."""
    changed = 0
    cur_tab = ""
    last_top = ""
    last_mid = ""

    for r in reqs:
        tab = r.tab or ""
        if tab != cur_tab:
            cur_tab = tab
            last_top = last_mid = ""

        detail = norm(r.detail)
        top = (r.top or "").strip()
        mid = (r.mid or "").strip()
        is_bullet = _is_bullet_detail(detail) or bool(_NUM_MID.match(detail))
        if len(sig(detail)) < 2 and not is_bullet:
            continue

        if is_bullet:
            if mid and mid != last_mid:
                last_top = top or last_top
                last_mid = mid
            elif last_mid and (top or mid):
                r.top = ""
                r.mid = ""
                changed += 1
            continue

        if top:
            last_top = top
            last_mid = mid if mid else ""
        elif mid:
            last_mid = mid
        else:
            last_top = last_mid = ""

    return reqs, changed


def refine_hierarchy(reqs: list[Req]) -> tuple[list[Req], list[str]]:
    """전체 계위 후처리 파이프라인."""
    steps: list[str] = []
    _, n = promote_table_mid_anchors(reqs)
    if n:
        steps.append(f"표 bullet anchor: {n}칸")
    _, n = collapse_bullet_groups(reqs)
    if n:
        steps.append(f"bullet 그룹 계위: {n}칸")
    _, n = enforce_gold_spacing(reqs)
    if n:
        steps.append(f"gold spacing: {n}칸")
    return reqs, steps
