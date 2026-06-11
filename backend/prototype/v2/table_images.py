"""파이프 표 → PNG + Req 후처리."""
from __future__ import annotations

import re
from copy import copy
from pathlib import Path

from .extract import Req
from .grid import _reflow_inline_pipe_table
from .table_render import _parse_pipe_table, split_detail_table_image


def _has_pipe_table(text: str) -> bool:
    if "|" not in text or "---" not in text:
        return False
    reflowed = _reflow_inline_pipe_table(text)
    return _parse_pipe_table(reflowed) is not None


def attach_pipe_table_images(reqs: list[Req], cache_dir: Path) -> tuple[list[Req], int]:
    """상세요건 내 마크다운 표 → PNG embed (표안표·중첩표 공통)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: list[Req] = []
    n = 0
    for i, r in enumerate(reqs):
        detail = _reflow_inline_pipe_table(r.detail)
        if not _has_pipe_table(detail):
            out.append(r)
            continue
        key = re.sub(r"[^\w\-]+", "_", r.rid or f"t{r.table_id}_{i}")[:40]
        text, imgs = split_detail_table_image(detail, cache_dir, key=key)
        if not imgs:
            out.append(r)
            continue
        if text.strip() and text.strip() != "[표]":
            tr = copy(r)
            tr.detail = text.strip()
            tr.detail_images = []
            out.append(tr)
        ir = copy(r)
        ir.detail = "[표]"
        ir.detail_images = list(r.detail_images) + imgs
        out.append(ir)
        n += 1
    return out, n
