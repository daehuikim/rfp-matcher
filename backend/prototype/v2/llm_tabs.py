"""
LLM 기반 탭(시트) 묶기 — 문서 섹션을 정답 조견표처럼 도메인 탭으로 군집.

heading 번호 깊이가 불균일해 구조만으로는 탭 입도를 정할 수 없다. 이 구조에
이미 있는 LLM 클라이언트로 PM 이 조견표 시트를 나누듯 섹션을 도메인 탭에 배정.
하드코딩된 탭 목록·폴백 규칙 없이, 섹션 자체를 LLM 이 판단한다.
"""
from __future__ import annotations

import asyncio
from collections import Counter

from pydantic import BaseModel

from app.core.config import Settings
from app.llm.base import Message
from app.llm.openai_client import OpenAIClient

from .extract import Req


class _KeepItem(BaseModel):
    index: int
    keep: bool


class _KeepResult(BaseModel):
    items: list[_KeepItem]


class _Assignment(BaseModel):
    index: int
    tab: str


class _TabResult(BaseModel):
    assignments: list[_Assignment]


def _client() -> OpenAIClient:
    s = Settings()
    return OpenAIClient(api_key=s.openai_api_key, model=s.llm_model_openai)


def _keep_prompt(sections: list[tuple[str, int, str]]) -> str:
    """1단계: 각 섹션이 '시스템 요구사항 명세'인지 keep/제외만 판정."""
    lines = "\n".join(
        f"[{i}] ({n}건) {path}\n      예시내용: {sample[:100]}"
        for i, (path, n, sample) in enumerate(sections)
    )
    return (
        "RFP 섹션들이다. 각 섹션이 '제안사가 구축할 시스템의 기능·기술 요구사항 명세'인지 "
        "**예시내용을 보고** 판정하라(헤딩 이름만으로 판단 금지).\n"
        "■ keep=true: 시스템의 기능·기술·데이터·보안·인프라·성능·UX·AI 명세. "
        "헤딩에 '프로젝트 업무'·'기술 요건'이 있어도 내용이 서버·DB·기능·연계·보안이면 keep.\n"
        "■ keep=false: 사업 개요/배경/목적/현황 설명, 제안 수행절차·일정, 가격·견적·대가, "
        "입찰·계약, 제안서 작성/제출/평가/서식, 조직·인력·교육 운영, 발주사 시스템 표준·"
        "구성도·HW 제품목록. (애매하면 keep=true)\n\n"
        f"[섹션]\n{lines}\n\n"
        '응답 JSON: {"items": [{"index": <int>, "keep": <bool>}, ...]} — 모든 index.'
    )


def _merge_prompt(kept: list[tuple[str, int, str]]) -> str:
    """2단계: keep 된 섹션만 도메인 탭으로 병합·명명."""
    lines = "\n".join(
        f"[{i}] ({n}건) {path}\n      예시내용: {sample[:80]}"
        for i, (path, n, sample) in enumerate(kept)
    )
    return (
        "아래는 요구사항 섹션들이다. 각 섹션을 **도메인 단위 탭 이름**에 배정하라.\n"
        "- **같은 도메인의 하위 절들은 반드시 한 탭으로 합친다.** 예: '정보처리시스템 정보보호', "
        "'개인정보처리 정보보호', 'AI 활용 정보보호', 'AI 플랫폼 보안' → 모두 **'정보보호 요청사항'** 한 탭. "
        "'프로젝트 업무 및 기술요건'·'ICT 인프라'는 서로 다른 도메인이면 별도.\n"
        "- 탭 이름은 섹션 번호 없이 간결한 도메인 명사구. 전체 탭 수는 보통 4~8개.\n\n"
        f"[섹션]\n{lines}\n\n"
        '응답 JSON: {"assignments": [{"index": <int>, "tab": "<탭이름>"}, ...]} — 모든 index.'
    )


async def assign_tabs(reqs: list[Req]) -> list[Req]:
    if not reqs:
        return reqs
    counts = Counter(r.section_path for r in reqs)
    sample: dict[str, str] = {}
    for r in reqs:
        if len(r.detail) > len(sample.get(r.section_path, "")):
            sample[r.section_path] = r.detail
    sections = [(p, n, sample.get(p, "")) for p, n in
                sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    client = _client()

    # 1단계: keep/제외
    keep_out = await client.structured_output(
        [Message(role="user", content=_keep_prompt(sections))], _KeepResult,
        purpose="tab_keep")
    kept_flag = {it.index: it.keep for it in keep_out.items}
    kept = [(i, sections[i]) for i in range(len(sections)) if kept_flag.get(i, True)]

    # 2단계: keep 된 섹션만 도메인 병합
    path_to_tab: dict[str, str] = {}
    if kept:
        kept_sections = [s for _i, s in kept]
        merge_out = await client.structured_output(
            [Message(role="user", content=_merge_prompt(kept_sections))], _TabResult,
            purpose="tab_merge")
        local_to_path = {j: kept[j][1][0] for j in range(len(kept))}
        for a in merge_out.assignments:
            if a.index in local_to_path:
                path_to_tab[local_to_path[a.index]] = a.tab.strip()

    for r in reqs:
        r.tab = path_to_tab.get(r.section_path, "")  # 제외/미배정은 빈 탭 → 드롭
    return [r for r in reqs if r.tab]


def assign_tabs_sync(reqs: list[Req]) -> list[Req]:
    return asyncio.run(assign_tabs(reqs))
