"""
리스트·문단 선행 제목 분리 — LLM이 '요건구분 제목' vs '상세 본문' 판단.

extract 전 prefetch 로 병렬 호출.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.llm.base import AsyncLlmClient, Message
from app.llm.factory import build_llm_client
from app.llm.fake_client import FakeLlmClient
from prototype.v2.async_run import run_coro
from prototype.v2.text import norm

from .financial_heading import preserve_group_label, split_title_body

logger = logging.getLogger(__name__)


class ListHeadingVerdict(BaseModel):
    has_title: bool = Field(description="앞부분이 요건구분용 짧은 제목이면 true")
    title: str = Field(default="", description="요건구분 열에 쓸 제목")
    body: str = Field(default="", description="상세내용 열 본문 — 원문 유지")


@dataclass
class FinancialListHeadingSplitter:
    use_llm: bool = True
    concurrency: int = 16
    _client: AsyncLlmClient | None = field(default=None, repr=False)
    _cache: dict[str, tuple[str, str]] = field(default_factory=dict)
    _errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.use_llm:
            return
        s = Settings()
        self.concurrency = max(2, self.concurrency or s.llm_concurrency)
        client = build_llm_client(s)
        if isinstance(client, FakeLlmClient):
            logger.info("financial_list: LLM unavailable — heuristic fallback")
            self.use_llm = False
        else:
            self._client = client

    def _cache_key(self, text: str) -> str:
        return norm(text)[:400]

    def _heuristic(self, text: str) -> tuple[str, str]:
        from .financial_heading import is_valid_req_group_label

        from .financial_heading import is_numbered_detail_item

        t = norm(text)
        if re.match(r"^[•·▪◦∙\-–—]", t) or is_numbered_detail_item(t):
            return "", t
        title, body = split_title_body(text)
        if title and is_valid_req_group_label(title):
            return title, body
        m = re.match(r"^(.{4,40}?)\s+(.{12,})$", t)
        if m:
            title, body = m.group(1).strip(), m.group(2).strip()
            if is_valid_req_group_label(title):
                return preserve_group_label(title), body
        return "", t

    async def _split_async(self, text: str) -> tuple[str, str]:
        prompt = (
            "RFP HTML 리스트/문단 **한 줄**에서 요건구분 제목과 상세 본문을 분리한다.\n\n"
            "[규칙]\n"
            "- has_title=true: 앞부분이 짧은 섹션·항목 제목(명사구), 뒤가 안내·요구 본문\n"
            "- '기타 요청사항'/'기타 요구사항' 뒤 서술은 **body** (요건구분에 넣지 말 것)\n"
            "- has_title=false: 전체가 하나의 상세 요구사항(불릿·①② 포함)이면 title 비움\n"
            "- title/body는 **원문 그대로** (요약·수정 금지)\n\n"
            f"[텍스트]\n{text[:800]}\n\n"
            'JSON: {"has_title": bool, "title": "", "body": "", "reason": ""}'
        )
        try:
            out = await self._client.structured_output(  # type: ignore[union-attr]
                [Message(role="user", content=prompt)],
                ListHeadingVerdict,
                purpose="financial_list_heading",
                max_tokens=500,
            )
            if out.has_title and out.title:
                return preserve_group_label(out.title), norm(out.body)
            return "", norm(text)
        except Exception as e:
            logger.warning("financial_list LLM failed: %s", e)
            self._errors.append(str(e)[:80])
            return self._heuristic(text)

    async def _prefetch_async(self, texts: list[str]) -> None:
        pending = [t for t in texts if self._cache_key(t) not in self._cache]
        if not pending or not self._client:
            return
        sem = asyncio.Semaphore(self.concurrency)

        async def one(text: str) -> None:
            key = self._cache_key(text)
            if key in self._cache:
                return
            async with sem:
                title, body = await self._split_async(text)
            self._cache[key] = (title, body)

        await asyncio.gather(*[one(t) for t in pending])

    def prefetch(self, texts: list[str]) -> int:
        before = len(self._cache)
        if self.use_llm and self._client:
            run_coro(self._prefetch_async(texts))
        else:
            for t in texts:
                key = self._cache_key(t)
                if key not in self._cache:
                    self._cache[key] = self._heuristic(t)
        return len(self._cache) - before

    def split(self, text: str) -> tuple[str, str]:
        key = self._cache_key(text)
        if key in self._cache:
            return self._cache[key]
        if self.use_llm and self._client:
            title, body = run_coro(self._split_async(text))
        else:
            title, body = self._heuristic(text)
        self._cache[key] = (title, body)
        return title, body


def make_list_heading_splitter(
    *, use_llm: bool = True, concurrency: int | None = None
) -> FinancialListHeadingSplitter:
    s = Settings()
    return FinancialListHeadingSplitter(
        use_llm=use_llm,
        concurrency=concurrency if concurrency is not None else s.llm_concurrency,
    )
