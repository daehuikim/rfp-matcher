"""
LLM 원자화 패스 — macro 추출(셀=1건) 결과에서 다불릿 셀을 LLM이 '요구사항 1건'
단위로 분할. 문서마다 다른 불릿 스타일(' - ' vs □ vs ①)을 LLM 판단으로 처리한다.
원문 그대로 분할만(요약·추가 금지). 배치 + 동시성으로 비용/시간 완화.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import replace

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.openai_client import OpenAIClient

from .extract import Req

_MARKERS = re.compile(r"[·•□◦▪○●∙‣①-⑩]")


class _CellResult(BaseModel):
    index: int
    atoms: list[str]


class _BatchOut(BaseModel):
    results: list[_CellResult]


def _is_multi(detail: str) -> bool:
    markers = len(_MARKERS.findall(detail)) + detail.count(" - ")
    return len(detail) > 50 and markers >= 2


def _prompt(batch: list[tuple[int, str]]) -> str:
    block = "\n".join(f"[{j}] {t[:1200]}" for j, (_i, t) in enumerate(batch))
    return (
        "다음은 RFP 조견표의 '상세요건' 셀들이다. 각 셀을 PM 검토용 '요구사항 1건' "
        "단위로 분해하라.\n"
        "- 셀 안의 불릿(- , □, ◦, ▪, ·, ①~⑩ 등) 한 항목 = 1건.\n"
        "- **원문 텍스트 그대로** 나눌 것. 요약·바꿔쓰기·내용 추가 금지.\n"
        "- 이미 1건이면 그대로 1개로 반환.\n\n"
        f"{block}\n\n"
        '응답 JSON: {"results": [{"index": <셀번호>, "atoms": ["원문조각", ...]}, ...]} '
        "— 입력 셀마다 1개씩."
    )


async def _run_batch(client: OpenAIClient, sem: asyncio.Semaphore,
                     batch: list[tuple[int, str]]) -> dict[int, list[str]]:
    async with sem:
        try:
            out = await client.structured_output(
                [Message(role="user", content=_prompt(batch))], _BatchOut,
                purpose="atomize")
        except Exception:
            return {}
    return {r.index: [a for a in r.atoms if a.strip()] for r in out.results}


async def atomize_reqs(reqs: list[Req], batch_size: int = 12,
                       concurrency: int = 6) -> list[Req]:
    targets = [(i, r.detail) for i, r in enumerate(reqs) if _is_multi(r.detail)]
    if not targets:
        return reqs
    s = Settings()
    client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)
    sem = asyncio.Semaphore(concurrency)
    batches = [targets[k:k + batch_size] for k in range(0, len(targets), batch_size)]
    results = await asyncio.gather(*[_run_batch(client, sem, b) for b in batches])

    expand: dict[int, list[str]] = {}
    for b, res in zip(batches, results):
        for j, (orig_i, text) in enumerate(b):
            atoms = res.get(j)
            expand[orig_i] = atoms if atoms else [text]

    out: list[Req] = []
    for i, r in enumerate(reqs):
        atoms = expand.get(i)
        if atoms and len(atoms) > 1:
            out.extend(replace(r, detail=a) for a in atoms)
        else:
            out.append(r)
    return out


def atomize_reqs_sync(reqs: list[Req]) -> list[Req]:
    return asyncio.run(atomize_reqs(reqs))
