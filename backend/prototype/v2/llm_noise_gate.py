"""row-level 노이즈 게이트 — 요청사항이 아닌 행을 gemma가 내용으로 판정해 드롭.

키워드 하드코딩(GPU 누락 등) 대신 LLM이 판단. detail 수정·생성 없음(드롭만 = 충실전사 유지).
드롭 대상: 발주사 보유 장비·SW 사양/모델 나열(인벤토리), 목차·색인, 사업 배경/목적/현황,
면책·안내 문구, 가격·일정·평가·서식. 애매하면 보존(요구사항 우선).
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.factory import build_llm_client

from .extract import Req
from .text import norm, sig


class _V(BaseModel):
    index: int
    is_requirement: bool
    reason: str


class _R(BaseModel):
    items: list[_V]


_PROMPT_HEAD = (
    "RFP에서 추출한 행들이다. 각 행이 **제안사가 수행·구축·충족·준수해야 할 실제 요구사항**인지 판정하라.\n\n"
    "is_requirement=false (노이즈/요청 아님):\n"
    "- 발주사가 보유·도입하는 장비/SW의 **사양·모델명·수량 나열**(예 'CPU Intel Xeon-Gold 64Core', "
    "'GPU H100 8장', 'Oracle 19c', 'Control-M', '용량 144TB' 처럼 제품/스펙/수량만 있는 행)\n"
    "- 목차/색인(……점선·번호 제목만), 사업 배경·목적·추진방향·기대효과·현황 설명\n"
    "- 면책·주의·안내 문구('변경될 수 있음', '본 목록은 예시', '아래와 같이 요청'), 가격·견적·일정·평가·서식·인사말\n\n"
    "is_requirement=true: 제안사가 만들/충족/제시해야 할 기능·성능·방안·지원·교육·인력·보안·관리·운영 요구. "
    "스펙 수치라도 '~이상 제공/구성해야' 처럼 제안사에 요구하면 true.\n"
    "판단 애매하면 **true(보존 우선)**. 내용만 보고 판정.\n"
)


def _render(batch: list[Req], start: int) -> str:
    return "\n".join(f"[{start + k}] {norm(r.detail)[:170]}" for k, r in enumerate(batch))


async def _classify(reqs: list[Req], concurrency: int = 8) -> dict[int, tuple[bool, str]]:
    client = build_llm_client(Settings())
    from app.llm.fake_client import FakeLlmClient

    if isinstance(client, FakeLlmClient):
        return {}
    sem = asyncio.Semaphore(concurrency)
    bs = 12
    batches = [(i, reqs[i:i + bs]) for i in range(0, len(reqs), bs)]
    verdict: dict[int, tuple[bool, str]] = {}

    async def one(start: int, batch: list[Req]) -> None:
        prompt = (
            _PROMPT_HEAD
            + "\n[행]\n"
            + _render(batch, start)
            + '\n\nJSON: {"items":[{"index":<int>,"is_requirement":<bool>,"reason":"<짧게>"}, ...]} — 모든 index.'
        )
        async with sem:
            try:
                out = await client.structured_output(
                    [Message(role="user", content=prompt)], _R, purpose="noise_gate"
                )
            except Exception:  # noqa: BLE001
                return
        for it in out.items:
            verdict[it.index] = (bool(it.is_requirement), it.reason.strip())

    await asyncio.gather(*[one(s, b) for s, b in batches])
    return verdict


def filter_noise_llm(reqs: list[Req]) -> tuple[list[Req], list[tuple[Req, str]]]:
    """gemma로 노이즈 행 드롭. (kept, dropped[(req,reason)]) 반환. 미판정은 보존."""
    from .async_run import run_coro

    verdict = run_coro(_classify(reqs))
    if not verdict:
        return reqs, []
    kept: list[Req] = []
    dropped: list[tuple[Req, str]] = []
    for i, r in enumerate(reqs):
        v = verdict.get(i)
        if v is None or v[0] or len(sig(r.detail)) < 2:
            kept.append(r)
        else:
            dropped.append((r, v[1]))
    return kept, dropped
