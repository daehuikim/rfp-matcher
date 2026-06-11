"""
금융 RFP HTML 구간 분류 — heading 청크마다 LLM이 수집·탭·요건구분·셀분해 모드 판단.

DOM walk 전 prefetch 로 병렬 호출해 지연을 줄인다.
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
from prototype.v2.tab_naming import is_bad_tab_name
from prototype.v2.async_run import run_coro
from prototype.v2.text import norm

from .financial_content import (
    is_context_section,
    is_reference_section,
    is_toc_line,
    text_reads_as_requirement,
)
from .financial_heading import preserve_group_label, preserve_tab_name

logger = logging.getLogger(__name__)

_FORM_NOISE = re.compile(r"서식|동의서|확인서|견적서|제안회사\s*일반", re.I)


class ZoneVerdict(BaseModel):
    collect: bool = Field(description="요구사항 추출 대상이면 true")
    tab: str = Field(default="", description="Excel 시트명 — 문서 구조에서 유도")
    subgroup: str = Field(default="", description="요건구분 열용 하위 제목")
    split_cells: bool = Field(
        default=False,
        description="①②③+불릿 단위 셀 분해(상세 표·리스트)면 true",
    )
    reason: str = ""


@dataclass
class ZoneState:
    collect: bool = False
    tab: str = ""
    subgroup: str = ""
    split_cells: bool = False


@dataclass(frozen=True)
class ZoneTask:
    heading: str
    level: int
    section_path: str
    preview: str = ""


@dataclass
class FinancialZoneClassifier:
    use_llm: bool = True
    concurrency: int = 16
    _client: AsyncLlmClient | None = field(default=None, repr=False)
    _cache: dict[str, ZoneState] = field(default_factory=dict)
    _errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.use_llm:
            return
        s = Settings()
        self.concurrency = max(2, self.concurrency or s.llm_concurrency)
        client = build_llm_client(s)
        if isinstance(client, FakeLlmClient):
            logger.info("financial_zone: LLM unavailable — structural fallback")
            self.use_llm = False
        else:
            self._client = client

    def _cache_key(self, section_path: str, heading: str) -> str:
        return f"{section_path}|{norm(heading)}"

    def _tab_from_path(self, section_path: str) -> str:
        for part in reversed(section_path.split(" > ")):
            p = norm(part)
            if not p or is_toc_line(p):
                continue
            name = preserve_tab_name(p)
            if name and name != "요구사항" and not is_bad_tab_name(name):
                return name
        return "요구사항"

    def _structural_fallback(
        self,
        heading: str,
        level: int,
        section_path: str,
        preview: str,
    ) -> ZoneState:
        h = norm(heading)
        if is_toc_line(h) or _FORM_NOISE.search(h) or is_context_section(h) or is_reference_section(h):
            return ZoneState(False, "", "", False)
        preview_n = norm(preview)
        has_req = (
            "•" in preview_n
            or "①" in preview_n
            or text_reads_as_requirement(preview_n)
        )
        has_detail_table = "요건" in preview_n and "구분" in preview_n and "상세" in preview_n
        if (has_req or has_detail_table) and level >= 4:
            tab = self._tab_from_path(section_path)
            sub = preserve_group_label(h) if level >= 5 else ""
            split = has_detail_table or "①" in preview_n
            return ZoneState(True, tab, sub, split)
        return ZoneState(False, "", "", False)

    async def _classify_async(
        self,
        *,
        heading: str,
        level: int,
        section_path: str,
        preview: str,
    ) -> ZoneState:
        prompt = (
            "금융 RFP 후처리 HTML의 **한 heading/절 구간**을 분류한다.\n\n"
            "[판단 기준]\n"
            "- collect=true: 제안사가 **구축·이행해야 할** 요구사항(• 불릿, ①②③, 요건표)\n"
            "- collect=false: 제안서 작성 안내·업체현황·H/W목록·가격·서식·목차, "
            "투입인력 이력·견적서, **사업추진배경·추진목적·기대효과·일정**, "
            "**당사 시스템 구성도·당사 시스템 표준·실증/평가 시나리오** 등 참고 자료\n"
            "- tab: Excel 시트명 = **N.N 절**(예: 1.4 프로젝트 범위, 2.3 수행방안). "
            "1.4.3 같은 하위 절은 1.4와 **같은 tab**. h6마다 시트 분리 금지.\n"
            "- subgroup: 요건구분 — **절 번호 포함 원문** (예: '2.7.4. 유지보수 이행방안'). 번호 제거 금지.\n"
            "- split_cells=true: ①②③+• 불릿을 한 상세 셀로 묶는 구간(요건|상세 표·①② 리스트)\n\n"
            f"section_path: {section_path}\n"
            f"heading(level={level}): {heading}\n"
            f"본문 미리보기:\n{preview[:900]}\n\n"
            'JSON: {"collect": bool, "tab": "", "subgroup": "", "split_cells": bool, "reason": ""}'
        )
        try:
            out = await self._client.structured_output(  # type: ignore[union-attr]
                [Message(role="user", content=prompt)],
                ZoneVerdict,
                purpose="financial_zone",
                max_tokens=400,
            )
            tab = preserve_tab_name(out.tab) if out.tab else ""
            sub = preserve_group_label(out.subgroup) if out.subgroup else ""
            if out.collect and not tab:
                tab = self._tab_from_path(section_path)
            return ZoneState(out.collect, tab, sub, out.split_cells)
        except Exception as e:
            logger.warning("financial_zone LLM failed: %s", e)
            self._errors.append(f"{heading[:40]}: {e}")
            return self._structural_fallback(heading, level, section_path, preview)

    async def _prefetch_async(self, tasks: list[ZoneTask]) -> None:
        pending = [
            t
            for t in tasks
            if self._cache_key(t.section_path, t.heading) not in self._cache
        ]
        if not pending or not self._client:
            return
        sem = asyncio.Semaphore(self.concurrency)

        async def one(task: ZoneTask) -> None:
            key = self._cache_key(task.section_path, task.heading)
            if key in self._cache:
                return
            async with sem:
                state = await self._classify_async(
                    heading=task.heading,
                    level=task.level,
                    section_path=task.section_path,
                    preview=task.preview,
                )
            self._cache[key] = state

        await asyncio.gather(*[one(t) for t in pending])

    def prefetch(self, tasks: list[ZoneTask]) -> int:
        """미분류 heading 일괄 LLM 호출. 반환: 새로 분류한 건수."""
        before = len(self._cache)
        if self.use_llm and self._client:
            run_coro(self._prefetch_async(tasks))
        else:
            for t in tasks:
                key = self._cache_key(t.section_path, t.heading)
                if key not in self._cache:
                    self._cache[key] = self._structural_fallback(
                        t.heading, t.level, t.section_path, t.preview
                    )
        return len(self._cache) - before

    def classify(
        self,
        *,
        heading: str,
        level: int,
        section_path: str,
        preview: str = "",
    ) -> ZoneState:
        key = self._cache_key(section_path, heading)
        if key in self._cache:
            return self._cache[key]
        if self.use_llm and self._client:
            state = run_coro(
                self._classify_async(
                    heading=heading,
                    level=level,
                    section_path=section_path,
                    preview=preview,
                )
            )
        else:
            state = self._structural_fallback(heading, level, section_path, preview)
        self._cache[key] = state
        return state


def make_zone_classifier(*, use_llm: bool = True, concurrency: int | None = None) -> FinancialZoneClassifier:
    s = Settings()
    return FinancialZoneClassifier(
        use_llm=use_llm,
        concurrency=concurrency if concurrency is not None else s.llm_concurrency,
    )
