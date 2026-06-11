"""
탭 검수 — 요구사항이 **아닌** 탭만 제거 (목차·안내·서식·제출절차 등).

※ 다른 탭과 주제/내용이 겹친다는 이유로는 절대 제거하지 않는다.
   제안서마다 범위·수행·관리·리스크 등이 유사하게 반복되는 것은 정상.
"""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.factory import build_llm_client

from .extract import Req

# 탭명만으로 비요구 — 결정적 제거 (LLM 호출 전)
_DROP_TAB_NAME = re.compile(
    r"(?:"
    r"제안서\s*작성|작성\s*기준|제출\s*(?:방법|서류)|평가\s*(?:및|및\s*)?선정|"
    r"기본\s*요건|유의\s*사항|기타\s*사항|"
    r"^서식$|목차|"
    r"입찰\s*제안|가격\s*제안|"
    r"제안\s*회사|일반\s*현황\s*(?:및|및\s*)?연혁"
    r")",
    re.I,
)

# 탭명에 있으면 요구/제안 응답 탭 — 결정적 보존
_KEEP_TAB_NAME = re.compile(
    r"(?:"
    r"요구|요건|기술요건|수행|관리|리스크|교육|기술지원|기술\s*이전|"
    r"인프라|보안|품질|아키텍처|범위|개발|구축|데이터|AI|UX|UI|"
    r"프로젝트|제안개요|제안\s*요청"
    r")",
    re.I,
)


class _Verdict(BaseModel):
    tab: str
    role: str
    keep: bool
    reason: str


class _Result(BaseModel):
    verdicts: list[_Verdict]


def _rows(items: list[Req], cap: int = 30) -> list[str]:
    n = len(items)
    idxs = list(range(n)) if n <= cap else sorted(
        {round(i * (n - 1) / (cap - 1)) for i in range(cap)})
    out = []
    for i in idxs:
        top = (items[i].top or "").strip()
        mid = (items[i].mid or "").strip()
        det = (items[i].detail or "").strip()
        head = " / ".join(x for x in (top, mid) if x and x not in det)
        out.append((f"{head} :: {det}" if head else det)[:200])
    return out


def _deterministic_drop(tab: str) -> bool | None:
    """True=제거, False=보존, None=LLM에 위임."""
    if _KEEP_TAB_NAME.search(tab):
        return False
    if _DROP_TAB_NAME.search(tab):
        return True
    return None


def _prompt(blocks: list[tuple[str, int, list[str]]]) -> str:
    secs = []
    for t, n, rows in blocks:
        body = "\n".join(f"    {i + 1}. {r}" for i, r in enumerate(rows))
        secs.append(f"### 탭 '{t}' ({n}건)\n{body}")
    listing = "\n\n".join(secs)
    return (
        "한 RFP 문서의 '탭'(시트 후보) 목록이다. **요구사항·제안응답 탭은 keep**, "
        "요구사항이 아닌 탭만 false.\n\n"
        "keep (요구/제안 응답):\n"
        "- 제안사가 무엇을 해야 하는지: 구축·구현·준수·제시·마련·수행·관리·교육·지원 등\n"
        "- 프로젝트 범위/수행방안/관리/리스크/기술지원/교육/인프라/보안 등 **모두 별도 keep**\n"
        "- **다른 탭과 주제가 겹쳐도 keep** (중복은 정상, drop 사유 아님)\n"
        "- 항목 수가 적어도(5~7건) 제안 요구면 keep\n\n"
        "drop (비요구만):\n"
        "- overview/background: 사업 배경·목적·필요성만 (제안사 행동 요구 없음)\n"
        "- toc/guide/process: 목차·작성안내·제출절차·평가방법·서식·입찰안내\n"
        "- vendor_form: 제안회사 일반현황·연혁 양식\n"
        "- status_only: **발주사 현황·사양 나열만** 있고 제안사 '~해야 함'이 전혀 없음\n\n"
        "금지: '다른 탭에 비슷한 내용이 있다'는 이유로 drop 하지 말 것.\n\n"
        f"{listing}\n\n"
        'JSON: {"verdicts":[{"tab":"<탭명>","role":"requirement|non_requirement",'
        '"keep":<bool>,"reason":"한 문장"}, ...]} — 모든 탭.'
    )


async def validate_tabs(reqs: list[Req], protected: set[str] | None = None,
                        drop_cap: float = 0.25) -> list[Req]:
    by_tab: OrderedDict[str, list[Req]] = OrderedDict()
    for r in reqs:
        by_tab.setdefault(r.tab, []).append(r)
    if len(by_tab) < 2:
        return reqs

    drop: set[str] = set()
    llm_blocks: list[tuple[str, int, list[str]]] = []

    for tab, items in by_tab.items():
        det = _deterministic_drop(tab)
        if det is True:
            drop.add(tab)
        elif det is False:
            pass
        else:
            llm_blocks.append((tab, len(items), _rows(items)))

    if llm_blocks:
        s = Settings()
        client = build_llm_client(s)
        try:
            prompt = _prompt(llm_blocks)
            out = await client.structured_output(
                [Message(role="user", content=prompt)], _Result,
                purpose="tab_validate", max_tokens=2500)
            from app.services.pipeline_logger import record_llm_io

            record_llm_io(
                "tab_validate",
                prompt=prompt,
                response=out,
                meta={"tabs": len(llm_blocks)},
            )
            for v in out.verdicts:
                if not v.keep and v.tab in by_tab:
                    if _deterministic_drop(v.tab) is False:
                        continue
                    drop.add(v.tab)
        except Exception:
            pass

    if protected:
        drop -= protected
    if not drop:
        return reqs

    drop_rows = sum(len(by_tab[t]) for t in drop)
    if drop_rows > len(reqs) * drop_cap:
        return reqs
    return [r for r in reqs if r.tab not in drop]


def validate_tabs_sync(reqs: list[Req], protected: set[str] | None = None) -> list[Req]:
    from .async_run import run_coro
    return run_coro(validate_tabs(reqs, protected))
