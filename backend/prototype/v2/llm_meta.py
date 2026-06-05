"""
메타데이터(계위 라벨) 생성 — 항목명/요구사항이 빈 행에 LLM 이 라벨만 부여.

LLM 출력 = 라벨(짧음)뿐, 상세 내용은 건드리지 않는다 → 손실·할루시 없음.
탭별로 상세 목록을 보여주고 각 행의 항목명(대분류)/요구사항(중분류)을 채우게 함.
연속 유사 항목은 같은 라벨로 묶어 계위를 만든다. 큰 탭은 청크로 처리.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.openai_client import OpenAIClient

from .extract import Req
from .text import norm

CHUNK = 25


class _Label(BaseModel):
    index: int
    top: str   # 항목명(대분류)
    mid: str   # 요구사항(중분류)


class _LabelResult(BaseModel):
    labels: list[_Label]


def _prompt(tab: str, rows: list[tuple[int, str, str, str]]) -> str:
    # rows: (index, 현재top, 현재mid, detail)
    block = "\n".join(
        f"[{i}] (항목명:{t or '?'} / 요구사항:{m or '?'}) {d[:90]}"
        for i, t, m, d in rows
    )
    return (
        f"도메인 '{tab}'의 요구사항 상세 목록이다. 각 항목에 **항목명(대분류)**과 "
        "**요구사항(중분류 제목)**을 부여해 계위를 만들어라.\n"
        "- 이미 값이 있으면(? 아님) 그대로 두고, 빈 것(?)만 채운다.\n"
        "- 연속된 유사 주제는 **같은 항목명/요구사항**으로 묶는다(병합 계위).\n"
        "- 라벨은 상세 내용에서 뽑은 간결한 명사구. **상세 내용 자체는 바꾸지 말 것**.\n\n"
        f"{block}\n\n"
        '응답 JSON: {"labels": [{"index": <int>, "top": "<항목명>", "mid": "<요구사항>"}, ...]} '
        "— 모든 index."
    )


async def _label_chunk(client: OpenAIClient, sem: asyncio.Semaphore, tab: str,
                       rows: list[tuple[int, str, str, str]]) -> dict[int, tuple[str, str]]:
    async with sem:
        try:
            out = await client.structured_output(
                [Message(role="user", content=_prompt(tab, rows))], _LabelResult,
                purpose="meta_label", max_tokens=4000)
        except Exception:
            return {}
    return {lb.index: (norm(lb.top), norm(lb.mid)) for lb in out.labels}


async def generate_metadata(reqs: list[Req], concurrency: int = 6) -> list[Req]:
    # 항목명 또는 요구사항이 빈 행이 있는 탭만 처리
    by_tab: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(reqs):
        if not r.top.strip() or not r.mid.strip():
            by_tab[r.tab].append(i)
    if not by_tab:
        return reqs

    s = Settings()
    client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)
    sem = asyncio.Semaphore(concurrency)

    tasks = []
    chunk_meta: list[list[int]] = []
    for tab, idxs in by_tab.items():
        # 해당 탭 전체 행(맥락) 중 빈 행 포함 구간을 청크로
        for k in range(0, len(idxs), CHUNK):
            chunk = idxs[k:k + CHUNK]
            rows = [(i, reqs[i].top, reqs[i].mid, reqs[i].detail) for i in chunk]
            tasks.append(_label_chunk(client, sem, tab, rows))
            chunk_meta.append(chunk)

    results = await asyncio.gather(*tasks)
    for res in results:
        for i, (top, mid) in res.items():
            if 0 <= i < len(reqs):
                if not reqs[i].top.strip() and top:
                    reqs[i].top = top
                    reqs[i].gen_top = True   # LLM 생성 → 셀 색 구분
                if not reqs[i].mid.strip() and mid:
                    reqs[i].mid = mid
                    reqs[i].gen_mid = True
    return reqs


def generate_metadata_sync(reqs: list[Req]) -> list[Req]:
    return asyncio.run(generate_metadata(reqs))
