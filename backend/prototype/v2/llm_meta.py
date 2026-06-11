"""
계위 라벨 생성 — LLM 이 항목명/요구사항을 **정답 조견표 형식**으로 부여.

- 도메인 그룹 첫 행만 항목명(top), 서브그룹 첫 행만 요구사항(mid)
- 연속 bullet 은 top/mid 빈칸 (셀 병합 형태)
- detail 은 절대 변경하지 않음
"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.factory import build_llm_client

from .extract import Req
from .text import norm

CHUNK = 24
_BULLET = re.compile(r"^\s*[-∙•·–—▪○●]")
_HANGUL_TOP = re.compile(r"^\s*[가-힣]\.\s+")
_NUM_MID = re.compile(r"^\s*\d+\)\s+")


class _Label(BaseModel):
    index: int
    top: str   # 항목명 — 연속행은 ""
    mid: str   # 요구사항 — 연속행은 ""


class _LabelResult(BaseModel):
    labels: list[_Label]


def _row_kind(detail: str) -> str:
    d = norm(detail)
    if _HANGUL_TOP.match(d):
        return "top_line"
    if _NUM_MID.match(d):
        return "mid_line"
    if _BULLET.match(d):
        return "bullet"
    return "plain"


def _prompt(tab: str, rows: list[tuple[int, str, str, str, str]], ctx: str = "") -> str:
    block = "\n".join(
        f"[{i}] kind={k} top={t or '-'} mid={m or '-'} | {d[:100]}"
        for i, k, t, m, d in rows
    )
    ctx_line = f"\n이전 청크 맥락:\n{ctx}\n" if ctx else ""
    return (
        f"RFP 조견표 탭 '{tab}' 요구사항 목록. **정답 Excel 형식**으로 항목명·요구사항 라벨을 부여하라.\n"
        f"{ctx_line}"
        "규칙 (매우 중요):\n"
        "1. **항목명(top)**: 큰 도메인 그룹의 **첫 행에만** 명사구. 같은 그룹 이어지는 행은 반드시 \"\".\n"
        "2. **요구사항(mid)**: 서브그룹 **첫 행에만** 제목. 같은 서브그룹 bullet 연속행은 \"\".\n"
        "3. kind=bullet 인 행은 직전 mid_line/bullet 그룹의 **연속**이면 top/mid 모두 \"\".\n"
        "4. kind=top_line 은 보통 새 항목명(top) anchor.\n"
        "5. kind=mid_line 은 보통 새 요구사항(mid) anchor.\n"
        "6. 내용 **맥락**으로 도메인 판단. 탭명 참고.\n"
        "7. detail 본문은 **절대 수정·요약 금지**. 라벨만 출력.\n"
        "8. **모든 index** 에 대해 top/mid 명시 (빈칸은 \"\").\n\n"
        f"{block}\n\n"
        'JSON: {"labels": [{"index": <int>, "top": "<항목명 또는 \"\">", "mid": "<요구사항 또는 \"\">"}, ...]}'
    )


async def _label_chunk(
    client,
    sem: asyncio.Semaphore,
    tab: str,
    rows: list[tuple[int, str, str, str, str]],
    ctx: str,
) -> dict[int, tuple[str, str]]:
    async with sem:
        try:
            prompt = _prompt(tab, rows, ctx)
            out = await client.structured_output(
                [Message(role="user", content=prompt)],
                _LabelResult,
                purpose="hierarchy_label",
                max_tokens=8000,
            )
            from app.services.pipeline_logger import record_llm_io

            record_llm_io(
                "hierarchy_label",
                prompt=prompt,
                response=out,
                meta={"tab": tab, "rows": len(rows)},
            )
        except Exception:
            return {}
    out_map: dict[int, tuple[str, str]] = {}
    for lb in out.labels:
        out_map[lb.index] = (norm(lb.top), norm(lb.mid))
    return out_map


def _apply_labels(reqs: list[Req], labels: dict[int, tuple[str, str]]) -> int:
    n = 0
    for i, (top, mid) in labels.items():
        if top != reqs[i].top:
            reqs[i].top = top
            reqs[i].gen_top = True
            n += 1
        if mid != reqs[i].mid:
            reqs[i].mid = mid
            reqs[i].gen_mid = True
            n += 1
    return n


async def assign_hierarchy_labels(reqs: list[Req], concurrency: int = 4) -> list[Req]:
    """탭별 전체 행에 LLM 계위 라벨 (정답 조견표 빈칸 형식)."""
    by_tab: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(reqs):
        if (r.tab or "").strip() in ("부록", "개요", ""):
            continue
        by_tab[r.tab].append(i)
    if not by_tab:
        return reqs

    s = Settings()
    if not s.openai_api_key:
        return reqs

    client = build_llm_client(s)
    sem = asyncio.Semaphore(concurrency)

    for tab, idxs in by_tab.items():
        ctx = ""
        recent_details: list[str] = []
        for k in range(0, len(idxs), CHUNK):
            chunk = idxs[k : k + CHUNK]
            rows = [
                (i, _row_kind(reqs[i].detail), reqs[i].top, reqs[i].mid, reqs[i].detail)
                for i in chunk
            ]
            labels = await _label_chunk(client, sem, tab, rows, ctx)
            missing = [i for i in chunk if i not in labels]
            if missing:
                retry_rows = [
                    (i, _row_kind(reqs[i].detail), reqs[i].top, reqs[i].mid, reqs[i].detail)
                    for i in missing
                ]
                labels.update(await _label_chunk(client, sem, tab, retry_rows, ctx))
            _apply_labels(reqs, labels)

            if chunk:
                last = reqs[chunk[-1]]
                recent_details = [reqs[i].detail[:80] for i in chunk[-3:]]
                ctx = (
                    f"마지막 계위: top={last.top or '-'}, mid={last.mid or '-'}\n"
                    + "최근 detail:\n"
                    + "\n".join(f"  - {d}" for d in recent_details)
                )

    return reqs


def assign_hierarchy_labels_sync(reqs: list[Req]) -> list[Req]:
    from .async_run import run_coro
    return run_coro(assign_hierarchy_labels(reqs))


async def generate_metadata(
    reqs: list[Req], concurrency: int = 6, *, top_only_if_both_empty: bool = False
) -> list[Req]:
    return await assign_hierarchy_labels(reqs, concurrency)


def generate_metadata_sync(reqs: list[Req], *, top_only_if_both_empty: bool = False) -> list[Req]:
    return assign_hierarchy_labels_sync(reqs)
