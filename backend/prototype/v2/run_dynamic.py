"""금융/비정형/스캔 공통 **동적 칼럼** 경로 — 입력→(OCR/HTML)→V2추출→섹션탭→계위→동적 writer.

financial_rfp + financial_excel_writer(고정 4칼럼)를 대체. 공공(public_form/korean_form)은 별도 유지.
reqs 에 `levels` 가 채워져 나오므로 writer 는 `dynamic_excel_writer.write_dynamic_excel` 사용.

원칙(사용자 지시):
- **100% recall**: pipeline.run(post_process=False)로 추출 원본만 받고, 행단위 노이즈 드롭 안 함
  (삭제는 사람 몫). boilerplate(목차/배경/일정/입찰/서식) 섹션만 LLM keep 이 **섹션단위**로 제외.
- **페이지순 보존(의미 병합 금지)**: reqs 를 page 안정정렬. 탭 = **상위 章(장) 단위 구조 그룹**(연속) —
  의미 기반 도메인 병합은 페이지순을 깨므로 하지 않는다. 말단 섹션은 section_levels 가 '대분류'
  칼럼으로 보존. 단일 섹션이 큰 경우(≥_LARGE_SECTION)는 자기 탭. writer 가 page 연속 run 단위 시트.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .ids import assign_ids
from .llm_meta import assign_hierarchy_labels_sync
from .llm_tabs import assign_section_tabs_sync
from .pipeline import run as _v2_run
from .section_levels import assign_section_levels

_LARGE_SECTION = 120  # 이 행수 이상인 단일 섹션은 병합 안 하고 자기 탭(사용자 '큰 섹션 따로')
_SEC_MARK = re.compile(r"^\s*(?:[IVXLCDM]+|\d+(?:\.\d+)*|[가-힣]|[①-⑳])[.)]\s*")


def _page_key(r):
    return r.page if getattr(r, "page", None) is not None else 9999


def _seg(section_path: str, idx: int) -> str:
    segs = [s.strip() for s in (section_path or "").split(">") if s.strip()]
    if not segs:
        return ""
    seg = segs[idx] if -len(segs) <= idx < len(segs) else segs[0]
    return _SEC_MARK.sub("", seg).strip()


def _regroup_tabs(reqs: list) -> None:
    """상위 章 구조 그룹(연속) — 탭 = **상위(부모) 섹션**, 말단 섹션은 section_levels 가 '대분류'
    칼럼으로 보존(depth 적응). 단일 섹션이 큰 경우(≥_LARGE_SECTION)는 자기 탭. **의미 병합 아님**
    (구조적·페이지 연속이라 페이지순 보존).

    예) BC '2.제안요청사항 > 라.서버요구/마.스토리지...' → 탭 '제안 요청 사항'(대분류=서버요구/스토리지).
        woori '2.제안요청범위 > 다.제안요건'(218행, 큼) → 자기 탭 '제안요건'.
        신한 'II>2.ICT요청사항>2.4.업무' → 탭 'ICT 요청사항'(대분류=2.4업무 등).
    """
    sp_count = Counter(r.section_path for r in reqs)
    for r in reqs:
        sp = r.section_path or ""
        leaf = _seg(sp, -1)
        if sp_count[sp] >= _LARGE_SECTION:
            r.tab = leaf or (r.tab or "요구사항")  # 큰 섹션 = 자기 탭(말단명)
        else:
            parent = _seg(sp, -2) if sp.count(">") >= 1 else leaf  # 상위 섹션으로 그룹
            r.tab = parent or leaf or (r.tab or "요구사항")


def finalize(reqs: list, overview: Any, steps: list[str]) -> dict[str, Any]:
    """추출 후처리 — 페이지순 정렬 → 섹션 keep → 상위章 그룹 → 계위 라벨 → 섹션계위 → ID.

    pipeline.run(post_process=False) 또는 OCR 추출 결과 reqs 를 받아 동적 writer 입력으로 마감.
    """
    n0 = len(reqs)
    reqs = sorted(reqs, key=_page_key)  # 페이지순(안정정렬 — 같은 페이지 내 원순서 유지)

    # LLM keep(boilerplate 섹션만 제외) — r.tab = leaf placeholder. 그 뒤 상위 章 구조 그룹.
    reqs = assign_section_tabs_sync(reqs)
    _regroup_tabs(reqs)  # 상위 章 단위(구조·연속), 말단=대분류칼럼, 큰 섹션은 자기 탭 (의미병합 아님)
    steps.append(f"섹션 keep+상위章 그룹: {n0}→{len(reqs)}행, {len({r.tab for r in reqs})}탭 (행단위 드롭 없음)")

    reqs = assign_hierarchy_labels_sync(reqs)  # 최종 탭 맥락에서 계위 라벨
    assign_section_levels(reqs, use_section_levels=True)
    assign_ids(reqs)  # 탭 기반 ID — 한 탭 = 한 접두사(일관)
    reqs = sorted(reqs, key=_page_key)  # 라벨/계위 후 페이지순 재보장
    steps.append(f"동적칼럼(페이지순): {len({r.tab for r in reqs})}탭")
    return {"reqs": reqs, "overview": overview, "steps": steps, "tab_order": None}


def run_dynamic(
    source: str | Path,
    gold: str | Path | None = None,
    *,
    log_session: Any = None,
) -> dict[str, Any]:
    """V2 공통 추출 + 동적 finalize. 반환: {reqs(levels 포함), overview, steps, report}."""
    m = _v2_run(
        str(source),
        str(gold) if gold else None,
        mode="llm",
        tab_mode="ordered",
        log_session=log_session,
        post_process=False,  # 후처리(탭/노이즈/계위/정렬/ids)는 finalize 가 — 100% recall·페이지순
    )
    reqs = m.get("_reqs") or []
    overview = m.get("_overview")
    steps = list(m.get("steps") or [])

    res = finalize(reqs, overview, steps)
    res["report"] = m.get("report")
    return res
