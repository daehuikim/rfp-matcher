"""
비요구 행 제거 — 발주사 현황·H/W 사양표·체크리스트 스펙 등.

요구사항 본문(~해야 함/제시/구축)이 아닌 **표 추출 노이즈**를 걸러낸다.
"""
from __future__ import annotations

import re

from .extract import Req
from .grid import Grid
from .schema import section_has_requirement_context
from .text import norm, sig

# □K8S Worker CPU 6530P … 형태
_HW_SPEC = re.compile(
    r"□.*(?:CPU|GPU|MEM|socket|Core|GHz|SSD|X\.86|X86|H200|NVMe)",
    re.I,
)
_CPU_LINE = re.compile(
    r"(?:CPU\s+\d{4}P|\d+Core\s+[\d.]+Ghz|2socket\s+MEM|내장\s+Disk)",
    re.I,
)
_REQ_VERB = re.compile(
    r"해야\s*함|하여야|제시|구축|구현|준수|마련|제공|수립|포함|가능",
)

_STATUS_SEC = re.compile(r"현황|사양|스펙|구성\s*현황|클러스터\s*현황", re.I)


def is_noise_detail(detail: str) -> bool:
    d = norm(detail)
    if len(sig(d)) < 2:
        return True
    if _HW_SPEC.search(d):
        return True
    if d.startswith("□") and _CPU_LINE.search(d) and not _REQ_VERB.search(d):
        return True
    # 숫자·단위만 나열 (건수/만건 표 일부)
    if re.fullmatch(r"[\d,.만억천+\s]+", d.replace(" ", "")):
        return True
    return False


def is_noise_row(r: Req) -> bool:
    if is_noise_detail(r.detail or ""):
        return True
    sec = r.section_path or ""
    if sec and not section_has_requirement_context(sec):
        if not _REQ_VERB.search(r.detail or ""):
            return True
    sp = (r.source or "") + sec
    if _STATUS_SEC.search(sec) or _STATUS_SEC.search(sp):
        if not _REQ_VERB.search(r.detail or ""):
            if _CPU_LINE.search(r.detail or "") or (r.detail or "").strip().startswith("□"):
                return True
    return False


def filter_noise_rows(reqs: list[Req]) -> tuple[list[Req], int]:
    kept = [r for r in reqs if not is_noise_row(r)]
    return kept, len(reqs) - len(kept)


def grid_looks_like_inventory(grid: Grid) -> bool:
    """H/W 사양·장비 목록 표 — 스키마 설계 전 빠른 판별."""
    blob = " ".join(
        grid.cells[r][c][:120]
        for r in range(min(grid.nrows, 12))
        for c in range(grid.ncols)
    )
    if _HW_SPEC.search(blob):
        return True
    if blob.count("CPU") >= 2 and blob.count("MEM") >= 2 and not _REQ_VERB.search(blob):
        return True
    return False
