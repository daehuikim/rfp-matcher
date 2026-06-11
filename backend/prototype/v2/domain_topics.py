"""
도메인 주제 coarsening — 탭 맥락을 LLM 이 읽고 대분류(항목명) 생성.

assign_ids 전·generate_metadata 전에 실행.
연속 bullet 은 도메인 첫 행만 top, 나머지 top 은 비움.
"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.llm.base import Message
from app.llm.factory import build_llm_client

from .extract import Req
from .text import norm

_SKIP_TABS = frozenset({"부록", "개요"})
_MAX_BLOCK = 22

# 탭명 기반 기본 도메인 (내용 규칙보다 우선 — 문서별 제너럴)
_TAB_DOMAIN: list[tuple[str, str]] = [
    (r"프로젝트\s*관리|품질보증", "프로젝트 관리"),
    (r"리스크", "리스크 관리"),
    (r"교육|기술이전", "교육 및 기술이전"),
    (r"기술지원", "기술지원"),
    (r"정보보호", "정보보호"),
    (r"거버넌스", "AI 거버넌스"),
    (r"UX|UI", "UXUI"),
    (r"인프라", "인프라"),
    (r"제안개요", "제안개요"),
    (r"수행방안|수행\s*방안", "프로젝트 수행"),
    (r"범위", "프로젝트 범위"),
]

_DOMAIN_RULES: list[tuple[str, str]] = [
    (r"지식베이스\(RAG\)|Knowledge\s*Base|RAG\s*(?:서비스|파이프)", "지식베이스(RAG)"),
    (r"비정형\s*데이터|Object\s*Storage|파싱|청킹|임베딩|VectorDB|Hybrid.?검색", "비정형 데이터 플랫폼"),
    (r"Agent.?플랫폼|AI Agent|AI Studio|Tool.?Repo|MCP|Lang.?graph", "Agent플랫폼"),
    (r"운영.?환경|지속가능|권한.?체계|가드레일", "운영환경"),
    (r"운영자동화|ICT운영", "운영자동화"),
    (r"백업|복구|장애|성능|서버|데이터베이스|OS|컨테이너|인터페이스|클러스터|쿠버네티스", "시스템 일반"),
    (r"정보처리|계정|권한|접근.?통제|취약점|암호", "정보처리시스템"),
    (r"개인정보|프라이버시", "개인정보처리시스템"),
    (r"AI.?플랫폼.?보안|생성형.?AI.?보안|프롬프트.?로그", "AI플랫폼보안"),
    (r"연계.?시스템|ESB|API.?게이트", "연계시스템"),
    (r"아키텍처|표준.?아키텍처", "아키텍처"),
    (r"데이터.?거버넌스|학습.?데이터|편향|인공지능.?기본법", "AI 거버넌스"),
]


def infer_domain(text: str, tab: str = "") -> str:
    if tab:
        for pat, domain in _TAB_DOMAIN:
            if re.search(pat, tab, re.I):
                return domain
    t = norm(text)
    if not t:
        return ""
    for pat, domain in _DOMAIN_RULES:
        if re.search(pat, t, re.I):
            return domain
    return ""


def _is_bullet_row(r: Req) -> bool:
    d = (r.detail or "").lstrip()
    return d.startswith("-") or d.startswith("∙") or d.startswith("•")


def _blocks_for_tab(reqs: list[Req], indices: list[int]) -> list[list[int]]:
    if not indices:
        return []
    blocks: list[list[int]] = []
    cur = [indices[0]]
    for i in indices[1:]:
        prev, row = reqs[cur[-1]], reqs[i]
        split = len(cur) >= _MAX_BLOCK
        if not split and prev.page and row.page and row.page - prev.page >= 2:
            split = True
        if not split and prev.table_id != row.table_id and prev.table_id >= 0 and row.table_id >= 0:
            split = True
        if split:
            blocks.append(cur)
            cur = [i]
        else:
            cur.append(i)
    if cur:
        blocks.append(cur)
    return blocks


def _canonical_domain(label: str, blob: str, tab: str = "") -> str:
    """LLM·소제목 라벨 → 정답 조견표 스타일 대분류."""
    tab_dom = infer_domain("", tab) if tab else ""
    if tab_dom:
        return tab_dom
    for text in (label, blob):
        dom = infer_domain(text, tab)
        if dom:
            return dom
    return norm(label)


def _block_blob(reqs: list[Req], idxs: list[int]) -> str:
    parts = []
    for i in idxs[:10]:
        r = reqs[i]
        parts.append(f"{r.top} {r.mid} {r.detail[:80]}")
    return " ".join(parts)


def _summarize_block(reqs: list[Req], idxs: list[int], bi: int, tab: str = "") -> str:
    blob = _block_blob(reqs, idxs)
    dom_hint = infer_domain(blob, tab) or "?"
    lines = []
    for i in idxs[:4]:
        r = reqs[i]
        lines.append(f"    mid={r.mid[:28] or '-'} | {r.detail[:64]}")
    extra = f"    … {len(idxs)}행" if len(idxs) > 4 else ""
    return f"[블록 {bi}] ({len(idxs)}행, 힌트={dom_hint})\n" + "\n".join(lines) + extra


class _DomainGroup(BaseModel):
    domain: str = Field(description="대분류 항목명 — 탭 내 재사용")
    id_prefix: str = Field(description="ID 접두사, 2~12자")
    blocks: list[int] = Field(description="포함 블록 index 목록")


class _TabDomains(BaseModel):
    groups: list[_DomainGroup]


def _prompt_tab(tab: str, blocks: list[list[int]], reqs: list[Req]) -> str:
    parts = [_summarize_block(reqs, idxs, bi, tab) for bi, idxs in enumerate(blocks)]
    return (
        f"RFP 조견표 탭 '{tab}' — {len(blocks)}개 블록을 **소수 대분류 도메인**으로 묶어라.\n"
        f"- 탭명 '{tab}' 이 주제 힌트. 탭 전체가 한 도메인이면 하나로 묶어도 됨.\n"
        "규칙:\n"
        "- **여러 블록을 하나의 domain 에 묶는 것이 기본** (블록마다 새 domain 금지).\n"
        "- domain 은 탭·내용에서 뽑은 간결한 명사구 (예: 프로젝트 범위, 비정형 데이터 플랫폼, 시스템 일반).\n"
        "- domain 은 항목명(대분류), id_prefix 는 짧은 ID용(시스템, 지식베이스, Agent플랫폼).\n"
        "- blocks 배열에 블록 index 를 나열. 모든 블록이 정확히 한 group 에 포함되어야 함.\n\n"
        + "\n\n".join(parts)
        + '\n\nJSON: {"groups": [{"domain":"...","id_prefix":"...","blocks":[0,1]}, ...]}'
    )


async def _label_tab(
    client,
    sem: asyncio.Semaphore,
    tab: str,
    blocks: list[list[int]],
    reqs: list[Req],
) -> dict[int, str]:
    if not blocks:
        return {}
    async with sem:
        try:
            out = await client.structured_output(
                [Message(role="user", content=_prompt_tab(tab, blocks, reqs))],
                _TabDomains,
                purpose="domain_coarsen",
                max_tokens=3000,
            )
        except Exception:
            return {}
    mapping: dict[int, str] = {}
    for g in out.groups:
        dom = norm(g.domain)
        if not dom:
            continue
        for bi in g.blocks:
            if 0 <= bi < len(blocks):
                mapping[bi] = dom
    return mapping


def _apply_domain_groups(
    reqs: list[Req],
    indices: list[int],
    blocks: list[list[int]],
    block_domain: dict[int, str],
) -> int:
    """도메인별 top anchor 1개만 — 연속 mid 는 유지."""
    if not indices:
        return 0
    row_domain: dict[int, str] = {}
    tab = reqs[indices[0]].tab if indices else ""
    for bi, idxs in enumerate(blocks):
        blob = _block_blob(reqs, idxs)
        raw = block_domain.get(bi, "")
        dom = _canonical_domain(raw, blob, tab) if raw else infer_domain(blob, tab)
        if not dom:
            continue
        for i in idxs:
            row_domain[i] = dom

    changed = 0
    cur_dom = ""
    for i in indices:
        dom = row_domain.get(i, cur_dom)
        if not dom:
            continue
        r = reqs[i]
        if dom != cur_dom:
            cur_dom = dom
            if r.top != dom:
                r.top = dom
                r.gen_top = True
                changed += 1
            continue
        # same domain — clear top on non-anchor rows
        if r.top:
            r.top = ""
            changed += 1
    return changed


def _deterministic_coarsen_tab(
    reqs: list[Req], indices: list[int], blocks: list[list[int]]
) -> int:
    block_domain: dict[int, str] = {}
    for bi, idxs in enumerate(blocks):
        tab = reqs[idxs[0]].tab if idxs else ""
        dom = infer_domain(_block_blob(reqs, idxs), tab)
        if dom:
            block_domain[bi] = dom
    return _apply_domain_groups(reqs, indices, blocks, block_domain)


async def coarsen_domain_topics(reqs: list[Req], concurrency: int = 4) -> tuple[list[Req], list[str]]:
    steps: list[str] = []
    by_tab: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(reqs):
        if r.tab in _SKIP_TABS:
            continue
        by_tab[r.tab].append(i)

    det_total = 0
    for tab, idxs in by_tab.items():
        blocks = _blocks_for_tab(reqs, idxs)
        det_total += _deterministic_coarsen_tab(reqs, idxs, blocks)
    if det_total:
        steps.append(f"도메인 규칙 coarsen: {det_total}칸")

    s = Settings()
    if not s.openai_api_key:
        steps.append("도메인 LLM: API 키 없음 — 규칙만 적용")
        return reqs, steps

    client = build_llm_client(s)
    sem = asyncio.Semaphore(concurrency)
    tasks = []
    meta: list[tuple[str, list[int], list[list[int]]]] = []
    for tab, idxs in by_tab.items():
        blocks = _blocks_for_tab(reqs, idxs)
        if len(blocks) <= 1:
            continue
        tasks.append(_label_tab(client, sem, tab, blocks, reqs))
        meta.append((tab, idxs, blocks))

    if not tasks:
        return reqs, steps

    results = await asyncio.gather(*tasks)
    for (tab, idxs, blocks), block_domain in zip(meta, results):
        if not block_domain:
            continue
        canon = {
            bi: _canonical_domain(block_domain[bi], _block_blob(reqs, blocks[bi]), tab)
            for bi in block_domain
        }
        n = _apply_domain_groups(reqs, idxs, blocks, canon)
        domains = set(canon.values())
        steps.append(f"도메인 LLM [{tab}]: {len(blocks)}블록 → {len(domains)}주제, {n}칸")

    return reqs, steps


def coarsen_domain_topics_sync(reqs: list[Req]) -> tuple[list[Req], list[str]]:
    return asyncio.run(coarsen_domain_topics(reqs))
