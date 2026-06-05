"""
개요(Overview) 첫 탭 — RFP 전체 요약 + 핵심 기술 + 핵심 RISK(독소조항).

- 전체 요약: PDF 전체 내용을 LLM 이 3~4줄로.
- 핵심 기술: 만들어진 조견표를 보고 기술별 스폿라이트(예: RAG: …).
- 핵심 RISK: 독소조항(예: 장기 유지보수 의무)을 찾아 관련 요구사항 ID 매핑.
요약은 PDF 에서, 기술·RISK 는 조견표(reqs)에서 뽑는다.
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.openai_client import OpenAIClient

from .extract import Req


class _Tech(BaseModel):
    name: str          # 기술명 (예: RAG)
    requirement: str   # 그 기술에 대한 요구 요약
    req_ids: list[str]  # 관련 요구사항 ID(조견표에서)


class _Risk(BaseModel):
    clause: str        # 독소조항/리스크 내용
    req_ids: list[str]  # 관련 요구사항 ID


class _Overview(BaseModel):
    summary: str           # 전체 요약 3~4줄
    techs: list[_Tech]
    risks: list[_Risk]


def _doc_text(doc, limit: int = 16000) -> str:
    if isinstance(doc, str):
        return doc[:limit]
    parts: list[str] = []

    def g(o):
        if isinstance(o, dict):
            c = o.get("content")
            if isinstance(c, str):
                parts.append(c)
            for v in o.values():
                g(v)
        elif isinstance(o, list):
            for v in o:
                g(v)

    g(doc)
    return " ".join(parts)[:limit]


def _reqs_text(reqs: list[Req], limit: int = 12000) -> str:
    lines = [f"[{r.rid}] {r.tab} | {r.top} | {r.mid} | {r.detail[:80]}" for r in reqs]
    return "\n".join(lines)[:limit]


def _prompt(doc_text: str, reqs_text: str) -> str:
    return (
        "아래 RFP 문서 본문과, 거기서 추출한 요구사항 조견표를 보고 '개요'를 작성하라.\n"
        "1) summary: 사업 전체를 3~4줄로 요약(무엇을 왜 구축하는지).\n"
        "2) techs: 핵심 기술별 스폿라이트. 기술명(RAG, Agent, 벡터검색 등) + 핵심 요구 한 줄 "
        "+ **그 기술과 실제로 관련된 조견표 [ID]들**(req_ids). 5~10개.\n"
        "3) risks: 제안사에 부담되는 **독소조항/리스크**(예: 장기 무상 유지보수 의무, "
        "과도한 책임, 짧은 납기 등). 각 risk 의 clause + **그 리스크와 실제로 관련된 [ID]만** req_ids 에. "
        "**중요: 근거가 약하면 억지로 만들지 말 것. 진짜 독소조항만, 관련 ID 도 내용이 맞는 것만. "
        "해당 없으면 risks 는 빈 배열.** 각 ID 의 실제 상세 내용이 clause 와 맞는지 확인하고 넣어라.\n\n"
        f"[RFP 본문(발췌)]\n{doc_text}\n\n"
        f"[요구사항 조견표]\n{reqs_text}\n\n"
        '응답 JSON: {"summary": "...", "techs": [{"name":"...","requirement":"..."}], '
        '"risks": [{"clause":"...","req_ids":["..."]}]}'
    )


async def _build(doc: dict, reqs: list[Req]) -> _Overview | None:
    s = Settings()
    client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)
    try:
        return await client.structured_output(
            [Message(role="user", content=_prompt(_doc_text(doc), _reqs_text(reqs)))],
            _Overview, purpose="overview", max_tokens=4000)
    except Exception:
        return None


def build_overview_sync(doc: dict, reqs: list[Req]) -> dict | None:
    ov = asyncio.run(_build(doc, reqs))
    if ov is None:
        return None
    # ID 는 실제 존재하는 것만 통과(할루시 ID 방지)
    valid_ids = {r.rid for r in reqs}

    def keep_ids(ids: list[str]) -> str:
        return ", ".join(i for i in ids if i in valid_ids)

    return {
        "summary": ov.summary,
        "techs": [(t.name, t.requirement, keep_ids(t.req_ids)) for t in ov.techs],
        "risks": [(r.clause, keep_ids(r.req_ids)) for r in ov.risks],
    }
