"""
결정적 계위(top/mid) carry-forward — LLM 메타 생성 전 적용.

표 페이지 넘김·HTML 표 분할로 빈 항목명/요구사항이 생기면 같은 탭·같은 표(또는
인접 페이지 연속)에서 이전 행 값을 이어받는다. gen_* 플래그는 세우지 않는다.
"""
from __future__ import annotations

from collections import defaultdict

import re

from .extract import Req

_BULLET = re.compile(r"^\s*[-∙•·–—]")


def _sort_key(r: Req) -> tuple:
  return (
    r.page if r.page is not None else 10**9,
    r.table_id if r.table_id is not None else 10**9,
  )


def carry_forward_hierarchy(reqs: list[Req]) -> tuple[list[Req], int]:
  """탭별 top/mid 결정적 이어받기. 반환: (reqs, 채운 필드 수)."""
  buckets: dict[str, list[Req]] = defaultdict(list)
  tab_order: list[str] = []
  for r in reqs:
    tab = (r.tab or "요구사항").strip() or "요구사항"
    if tab not in buckets:
      tab_order.append(tab)
    buckets[tab].append(r)

  filled = 0
  for tab in tab_order:
    items = buckets[tab]
    items.sort(key=_sort_key)
    last_top = ""
    last_mid = ""
    last_page: int | None = None
    last_table: int | None = None

    for r in items:
      same_table = last_table is not None and r.table_id == last_table
      page_cont = (
        last_page is not None
        and r.page is not None
        and r.page in (last_page, last_page + 1)
      )
      can_carry = same_table or page_cont or last_table is None
      bullet_cont = (
        bool(_BULLET.match((r.detail or "").strip()))
        and not (r.top or "").strip()
        and not (r.mid or "").strip()
      )

      if not r.top.strip():
        if last_top and can_carry and not bullet_cont:
          r.top = last_top
          filled += 1
      else:
        if r.top.strip() != last_top:
          last_mid = ""
        last_top = r.top.strip()

      if not r.mid.strip():
        if last_mid and can_carry and not bullet_cont:
          r.mid = last_mid
          filled += 1
      else:
        last_mid = r.mid.strip()

      last_page = r.page
      last_table = r.table_id

  return reqs, filled
