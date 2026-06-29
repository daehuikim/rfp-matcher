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
from app.llm.base import AsyncLlmClient, Message
from app.llm.factory import build_llm_client

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


def _client() -> AsyncLlmClient:
    return build_llm_client(Settings())


def _keep_prompt(sections: list[tuple[str, int, str]]) -> str:
    """1단계: 각 섹션이 '시스템 요구사항 명세'인지 keep/제외만 판정."""
    lines = "\n".join(
        f"[{i}] ({n}건) {path}\n      예시내용: {sample[:100]}"
        for i, (path, n, sample) in enumerate(sections)
    )
    return (
        "RFP 섹션들이다. 조견표에 **넣을지(keep)** 판정하라. 원칙: **삭제는 사람이 한다 — 조금이라도 "
        "제안사가 이행·수행·준수할 내용이면 무조건 keep**(과포함이 누락보다 낫다). 예시내용을 보고 판정.\n"
        "■ keep=true (기본값): 기능·기술·데이터·보안·인프라·성능·AI 명세는 물론, **수행방안·관리방안·품질·"
        "리스크·교육·기술이전·투입인력·운영지원·유지보수·지원방안·'기타사항'·'기타 요구사항'·준수사항·"
        "제약조건** 등 제안 이행 관련이면 전부 keep.\n"
        "■ keep=false 는 **아래 '명백한 비요구'만**(이것 외엔 전부 keep): "
        "①표지·문서 목차(점선 …… 나열)·제안서 작성목차/서식양식 ②사업 배경·목적·추진방향 설명문 "
        "③추진일정표·일정 ④입찰 참가절차·자격·계약·제출처·담당자 연락처 ⑤제안 평가·배점 기준 "
        "⑥발주사가 **현재 보유·운영 중**인 **AS-IS 현황 '목록표'**(제안사 이행대상 아님): 사업장/사무소/지사 주소·연락처 "
        "나열, 보유 SW·HW **제품명+버전 인벤토리**, 조직도·인력·정원 현황, 현재 시스템·네트워크 구성도, 예산·사업비 내역. "
        "⑦**별첨 제출 양식**(입찰참가신청서·공정입찰/이행 서약서·비밀유지/기밀보호 협약서·보안서약서·업체신고서·"
        "재무제표/매출/3년 수행실적/투입인력 이력 양식·가격제안서/산출근거 서식 등 서명·날인·___ 빈칸 채우는 제출 양식) "
        "⑧제안서 **제출 안내**(제출서류 목록·제출방법·부수·USB/이메일 제출) "
        "⑨**집계·통계표**(요구사항 개수 총괄표·분류별 건수 등 실제 요구내용 없이 숫자만 집계) 및 "
        "단독 숫자/코드·버전문자열만 든 셀 파편(예: '100', '94.0.4606.81'). \n"
        "■ **결정적 구분(over-drop 방지)**: **제안사(사업자)가 납품·제출·설계·구현·구성·포함·지원·준수**하는 **행위 문장**이면 "
        "SW/HW/라이선스/장비/데이터를 언급해도 **무조건 keep**(현황·집계로 보지 말 것). "
        "예 keep: '사업자는 라이선스 증서를 제출하여야 함', 'S/W 사용료 3년 포함', '서버 납품 시 공통사항 준수', "
        "'녹취 데이터 관리방안 설계', '주관기관 정보보호시스템 구축에 적용되는 요구사항', '누출금지 대상정보(외부IP·소스코드 등)'. "
        "⑥⑨ 드롭은 **명사·제품명·버전·주소가 나열된 순수 목록/숫자표**에만 적용한다.\n"
        "**판단이 조금이라도 애매하면 반드시 keep=true.** ('기타사항'·'지원방안'·'유의사항(준수조건)'은 keep) "
        "단 위 ⑤⑥⑦⑨(평가·배점·AS-IS 현황목록·제출양식·집계표)에 **명백히** 해당하면 keep=false.\n\n"
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
        "서버/스토리지/네트워크/장비 요구 → '인프라 및 장비', 품질/일정/변경/이슈/인력/산출물 관리 → '프로젝트 관리' 처럼 묶는다.\n"
        "- **행수(건)가 많은 큰 섹션(대략 100건 이상)은 독립 탭으로 둔다**(다른 섹션과 합치지 말 것).\n"
        "- 탭 이름은 섹션 번호 없이 간결한 도메인 명사구. **전체 탭 수는 5~9개** 목표.\n\n"
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


# ── 페이지연속 섹션탭: keep(LLM)만, 탭명=섹션 헤딩(merge 없음=충실·결정론·페이지순) ──────────
import re as _re  # noqa: E402

_SEC_MARK_RX = _re.compile(r"^\s*(?:[IVXLCDM]+|\d+(?:\.\d+)*|[가-힣]|[①-⑳])[.)]\s*")


def _section_tab_name(section_path: str) -> str:
    """keep 단계 placeholder 탭명 = 말단(leaf) 섹션(번호 제거). 실제 탭 레벨링(부모 병합/큰섹션
    분리)은 run_dynamic.finalize 가 한다 — 여기선 kept 여부 표시용으로 leaf 를 쓴다."""
    segs = [s.strip() for s in (section_path or "").split(">") if s.strip()]
    if not segs:
        return "요구사항"
    return _SEC_MARK_RX.sub("", segs[-1]).strip() or "요구사항"


async def assign_section_tabs(reqs: list[Req]) -> list[Req]:
    """탭 = 문서 섹션 헤딩(페이지연속). LLM 은 keep(요구영역 vs boilerplate)만 판정(merge 안 함).

    같은 도메인 병합/도메인명 작명을 하지 않아 결정론적·충실(섹션이 곧 탭). boilerplate 섹션만
    drop, 나머지(기타사항·지원방안 등)는 각자 섹션탭 유지 → 100% recall + 페이지순.
    """
    if not reqs:
        return reqs
    counts = Counter(r.section_path for r in reqs)
    sample: dict[str, str] = {}
    for r in reqs:
        if len(r.detail) > len(sample.get(r.section_path, "")):
            sample[r.section_path] = r.detail
    sections = [
        (p, n, sample.get(p, ""))
        for p, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    client = _client()
    keep_out = await client.structured_output(
        [Message(role="user", content=_keep_prompt(sections))], _KeepResult,
        purpose="section_keep")
    kept_flag = {it.index: it.keep for it in keep_out.items}
    kept_paths = {sections[i][0] for i in range(len(sections)) if kept_flag.get(i, True)}
    for r in reqs:
        r.tab = _section_tab_name(r.section_path) if r.section_path in kept_paths else ""
    return [r for r in reqs if r.tab]


def assign_section_tabs_sync(reqs: list[Req]) -> list[Req]:
    return asyncio.run(assign_section_tabs(reqs))


# ── 평면 리스트 문서용: 내용기반 도메인 탭 군집 ─────────────────────────────
# 평면 문서(요구사항이 한 헤딩 아래 평탄)는 section_path 가 한 값이라 위 assign_tabs 로는
# 못 나뉜다. 이 경우 PM 이 조견표 시트를 나누듯, 각 요구사항 detail 을 LLM 이 읽어
# 도메인 탭에 직접 배정한다. 하드코딩 탭 목록 없이 LLM 이 군집·명명(예시는 안내일 뿐).
_CONTENT_TAB_CHUNK = 40


def _content_tab_prompt(rows: list[tuple[int, str]], tabs_so_far: list[str]) -> str:
    block = "\n".join(f"[{i}] {d[:140]}" for i, d in rows)
    ctx = (
        f"\n이미 만든 탭(가능하면 재사용, 새 도메인이면 새로 추가): {', '.join(tabs_so_far)}\n"
        if tabs_so_far else ""
    )
    return (
        "RFP의 상세요건들을 **도메인 단위 시트(탭)**로 분류하라. 한 문서 전체를 보통 4~8개 탭으로 묶는다.\n"
        "- 같은 주제의 연속/유사 항목은 **반드시 같은 탭**(예: 생성형AI 기능, RAG·지식베이스, 정보보안, "
        "데이터·시스템 연계, 인프라·운영, 프로젝트 관리·인력 — 이건 예시일 뿐, 실제 내용에 맞는 도메인명을 직접 정하라).\n"
        "- 탭이 너무 잘게 쪼개지지 않게 한다(한 항목=한 탭 금지). 탭 이름은 번호 없는 간결한 도메인 명사구.\n"
        f"{ctx}\n[요구사항]\n{block}\n\n"
        '응답 JSON: {"assignments":[{"index":<int>,"tab":"<탭이름>"}, ...]} — 모든 index 포함.'
    )


async def assign_content_tabs(reqs: list[Req]) -> list[Req]:
    """평면 문서: detail 내용으로 LLM 이 도메인 탭 군집(청크+러닝컨텍스트로 탭명 일관)."""
    if not reqs:
        return reqs
    client = _client()
    tabs_so_far: list[str] = []
    for k in range(0, len(reqs), _CONTENT_TAB_CHUNK):
        chunk = list(range(k, min(k + _CONTENT_TAB_CHUNK, len(reqs))))
        rows = [(i, reqs[i].detail) for i in chunk]
        try:
            out = await client.structured_output(
                [Message(role="user", content=_content_tab_prompt(rows, tabs_so_far))],
                _TabResult, purpose="content_tab")
        except Exception:  # noqa: BLE001
            continue
        for a in out.assignments:
            if 0 <= a.index < len(reqs):
                reqs[a.index].tab = (a.tab or "").strip()
        tabs_so_far = sorted({r.tab for r in reqs if r.tab})
    # 미배정 행은 직전 탭 상속(연속성), 처음부터 비면 '기타 요건'
    last = ""
    for r in reqs:
        if r.tab:
            last = r.tab
        else:
            r.tab = last or "기타 요건"
    return reqs


def assign_content_tabs_sync(reqs: list[Req]) -> list[Req]:
    return asyncio.run(assign_content_tabs(reqs))
