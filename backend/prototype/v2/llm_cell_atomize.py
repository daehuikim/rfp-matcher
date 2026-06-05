"""
셀/행 원자화 — opendataloader가 표의 '평문 헤더 + 불릿'을 grid 행 단위로 쪼개거나
다음 헤더를 앞 불릿 끝에 붙여, 헤더가 단독 행이 되거나 글루되는 문제를 LLM(few-shot)으로 교정.

대상: '항목명(짧은 비문장 명사구) + 상세 요구(문장)'가 섞인 표만(안전 게이트).
정상 표(헤더 패턴 없음)는 건드리지 않는다. 실패 시 원본 보존.
"""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from dataclasses import replace

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.openai_client import OpenAIClient

from .extract import Req
from .text import sig

_SENT_END = re.compile(r"(다|요|함|음|됨|오|니다)\.?$|[.。:;,)\]]$")


def _is_header(text: str) -> bool:
    """짧은 비문장 명사구 = 항목명 헤더 후보."""
    t = (text or "").strip()
    if not (1 < len(t) <= 22):
        return False
    return not _SENT_END.search(t)


def _glued_header(text: str) -> bool:
    """'…합니다. 지원 파일 형식'처럼 문장 끝에 짧은 헤더가 붙은 형태."""
    m = re.match(r"^.*(?:다|요|함|음|됨|니다)[.。]?\s+([^.。\n]{2,22})$", text or "")
    return bool(m and _is_header(m.group(1)))


def _needs(rows: list[Req]) -> bool:
    """표에 '헤더 패턴'(단독 헤더 행 or 글루 헤더)이 2건 이상이면 재구성 대상."""
    hdr = sum(1 for r in rows if _is_header(r.detail) or _glued_header(r.detail))
    return hdr >= 2 and len(rows) >= 3


class _Item(BaseModel):
    header: str
    detail: str


class _Out(BaseModel):
    items: list[_Item]


_FEWSHOT = (
    "표 한 묶음에서 추출된 줄들이 순서대로 주어진다. 일부 줄은 '항목명(헤더)'이고 일부는 '상세 요구'다.\n"
    "헤더는 짧은 명사구(예: '원천 시스템 연계','지원 파일 형식')로 문장이 아니다. 헤더가 앞 줄 끝에\n"
    "붙어있을 수 있다('…제공해야 합니다. 지원 파일 형식'). 이를 (header, detail) 원자 목록의 JSON으로 재구성하라:\n"
    "- 헤더는 단독 item으로 만들지 말고, 그 아래 상세 요구들의 header 값으로 carry-forward.\n"
    "- 줄 끝에 붙은 다음 헤더는 떼어내 그 다음 상세들의 header로.\n"
    "- 한 상세 요구 = 한 item. 헤더 없으면 header=\"\". 상세 본문(detail)은 원문 그대로 유지.\n\n"
    "[예시 입력]\n"
    "  1. 인증 관리\n"
    "  2. 사용자 인증은 SSO를 지원해야 합니다.\n"
    "  3. 다중 인증(MFA)을 제공해야 합니다. 권한 관리\n"
    "  4. 역할 기반 접근제어(RBAC)를 적용해야 합니다.\n"
    "[예시 출력]\n"
    '  {"items":[\n'
    '    {"header":"인증 관리","detail":"사용자 인증은 SSO를 지원해야 합니다."},\n'
    '    {"header":"인증 관리","detail":"다중 인증(MFA)을 제공해야 합니다."},\n'
    '    {"header":"권한 관리","detail":"역할 기반 접근제어(RBAC)를 적용해야 합니다."}\n'
    "  ]}\n\n"
)


async def _restructure(client, rows: list[Req]) -> list[Req] | None:
    body = "\n".join(f"  {i + 1}. {r.detail.strip()}" for i, r in enumerate(rows))
    try:
        out = await client.structured_output(
            [Message(role="user", content=_FEWSHOT + "아래 [입력]을 위 형식의 JSON으로 재구성하라.\n[입력]\n" + body)],
            _Out, purpose="cell_atomize", max_tokens=2000)
    except Exception:
        return None
    items = [it for it in out.items if sig(it.detail) and len(sig(it.detail)) >= 2]
    if not items:
        return None
    # 원자 본문 합이 원본의 절반 미만이면(내용 유실 의심) 폐기 → 원본 보존
    if sum(len(sig(it.detail)) for it in items) < 0.5 * sum(len(sig(r.detail)) for r in rows):
        return None
    base = rows[0]
    top = (base.top or "").strip()
    new = []
    for it in items:
        new.append(replace(
            base, top=top, mid=it.header.strip(), detail=it.detail.strip(),
            gen_rid=False, gen_top=False, gen_mid=False))
    return new


async def atomize_cells(reqs: list[Req]) -> list[Req]:
    by_tab: "OrderedDict[int, list[Req]]" = OrderedDict()
    order: list = []
    for r in reqs:
        key = r.table_id
        if key not in by_tab:
            by_tab[key] = []
            order.append(key)
        by_tab[key].append(r)

    targets = [k for k in order if by_tab[k] and _needs(by_tab[k])]
    if not targets:
        return reqs

    s = Settings()
    client = OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)
    sem = asyncio.Semaphore(max(2, s.llm_concurrency))

    async def _run(k):
        async with sem:
            return k, await _restructure(client, by_tab[k])

    fixed = dict(await asyncio.gather(*[_run(k) for k in targets]))
    out: list[Req] = []
    for k in order:
        rep = fixed.get(k)
        out.extend(rep if rep else by_tab[k])
    return out


def atomize_cells_sync(reqs: list[Req]) -> list[Req]:
    return asyncio.run(atomize_cells(reqs))
