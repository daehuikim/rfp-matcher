"""
전역 content 기반 탭 클러스터링 — LLM 이 '전체 요구사항'을 보고 탭을 나눈다.

표를 하나씩만 보면 도메인이 뭉뚱그려진다(예: 전부 '데이터'). 그래서:
  1) 전체 요구사항을 넓게 샘플링 → LLM 이 도메인 탭 체계(taxonomy) 설계.
  2) 각 요구사항을 그 체계의 한 탭에 배정(또는 제외). 출력은 라벨(작음).
LLM 은 '나누는 기준(스키마)'만 정하고, 내용은 안 바꾼다.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.openai_client import OpenAIClient

from .extract import Req

ASSIGN_CHUNK = 40


class _TabDef(BaseModel):
    name: str
    desc: str


class _Taxonomy(BaseModel):
    tabs: list[_TabDef]


class _Assign(BaseModel):
    index: int
    tab: str


class _AssignResult(BaseModel):
    assignments: list[_Assign]


def _line(i: int, r: Req) -> str:
    return f"[{i}] {r.top} | {r.mid} | {r.detail[:80]}"


def _taxonomy_prompt(sample: list[tuple[int, Req]]) -> str:
    block = "\n".join(_line(i, r) for i, r in sample)
    return (
        "아래는 한 RFP 에서 추출한 요구사항 상세 샘플이다. 사람이 요구사항 조견표를 "
        "만들 때처럼 **도메인 탭 체계**를 설계하라.\n"
        "- 내용을 읽고 5~10개의 도메인 탭으로 나눈다(예: 데이터 수집/연계, 검색/RAG, "
        "보안/정보보호, 인프라, UX/UI, AI 거버넌스, 성능/품질 등 — 실제 내용 기준).\n"
        "- 각 탭은 name(간결한 도메인 명사구) + desc(어떤 요구가 들어가는지 한 줄).\n"
        "- 절차/현황/가격/조직 같은 비요구는 탭으로 만들지 않는다.\n\n"
        f"[요구사항 샘플]\n{block}\n\n"
        '응답 JSON: {"tabs": [{"name": "...", "desc": "..."}, ...]}'
    )


def _assign_prompt(tabs: list[_TabDef], chunk: list[tuple[int, Req]]) -> str:
    taxo = "\n".join(f"- {t.name}: {t.desc}" for t in tabs)
    block = "\n".join(_line(i, r) for i, r in chunk)
    return (
        "각 요구사항을 아래 탭 중 **가장 알맞은 하나**에 배정하라. 시스템 요구사항이 "
        "아니면(절차/현황/가격/조직/인사말) tab=\"제외\".\n\n"
        f"[탭 목록]\n{taxo}\n\n[요구사항]\n{block}\n\n"
        '응답 JSON: {"assignments": [{"index": <int>, "tab": "<탭name 또는 제외>"}, ...]} — 모든 index.'
    )


async def cluster_tabs(reqs: list[Req], concurrency: int = 6) -> list[Req]:
    if len(reqs) < 2:
        for r in reqs:
            r.tab = r.tab or "요구사항"
        return reqs
    s = Settings()
    client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)

    # 1) taxonomy: 전체를 넓게 샘플(최대 120)
    step = max(1, len(reqs) // 120)
    sample = [(i, reqs[i]) for i in range(0, len(reqs), step)][:120]
    taxo = await client.structured_output(
        [Message(role="user", content=_taxonomy_prompt(sample))], _Taxonomy,
        purpose="tab_taxonomy", max_tokens=1500)
    tabs = taxo.tabs or [_TabDef(name="요구사항", desc="전체")]
    valid = {t.name for t in tabs}

    # 2) 각 요구사항을 탭에 배정(청크 병렬)
    sem = asyncio.Semaphore(concurrency)

    async def assign_chunk(chunk: list[tuple[int, Req]]) -> dict[int, str]:
        async with sem:
            try:
                out = await client.structured_output(
                    [Message(role="user", content=_assign_prompt(tabs, chunk))],
                    _AssignResult, purpose="tab_assign", max_tokens=4000)
            except Exception:
                return {}
        return {a.index: a.tab.strip() for a in out.assignments}

    chunks = [[(i, reqs[i]) for i in range(k, min(k + ASSIGN_CHUNK, len(reqs)))]
              for k in range(0, len(reqs), ASSIGN_CHUNK)]
    results = await asyncio.gather(*[assign_chunk(c) for c in chunks])

    keep: list[Req] = []
    for res in results:
        pass
    merged: dict[int, str] = {}
    for res in results:
        merged.update(res)
    for i, r in enumerate(reqs):
        tab = merged.get(i, "")
        if tab in valid:
            r.tab = tab
            keep.append(r)
        elif tab == "제외":
            continue
        else:  # 미배정/오타 → 가장 가까운 기존 탭 없으면 첫 탭
            r.tab = tabs[0].name
            keep.append(r)
    return keep


def cluster_tabs_sync(reqs: list[Req]) -> list[Req]:
    return asyncio.run(cluster_tabs(reqs))
