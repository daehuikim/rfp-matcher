"""금융/비정형/스캔 공통 **동적 칼럼** 경로 — 입력→(OCR/HTML)→V2추출→섹션탭→계위→동적 writer.

financial_rfp + financial_excel_writer(고정 4칼럼)를 대체. 공공(public_form/korean_form)은 별도 유지.
reqs 에 `levels` 가 채워져 나오므로 writer 는 `dynamic_excel_writer.write_dynamic_excel` 사용.

원칙(사용자 지시):
- **100% recall**: pipeline.run(post_process=False)로 추출 원본만 받고, 행단위 노이즈 드롭 안 함
  (삭제는 사람 몫). boilerplate(목차/배경/일정/입찰/서식) 섹션만 LLM keep 이 **섹션단위**로 제외.
- **페이지순 보존**: reqs 를 page 기준 안정정렬, 탭 = 문서 섹션 헤딩(merge/도메인작명 안 함=결정론·충실),
  writer 가 page 연속 run 단위 시트 생성(도메인 재군집으로 순서 흐트러뜨리지 않음).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .ids import assign_ids
from .llm_meta import assign_hierarchy_labels_sync
from .llm_tabs import assign_section_tabs_sync
from .pipeline import run as _v2_run
from .section_levels import assign_section_levels


def _page_key(r):
    return r.page if getattr(r, "page", None) is not None else 9999


def finalize(reqs: list, overview: Any, steps: list[str]) -> dict[str, Any]:
    """추출 후처리 — 페이지순 정렬 → 섹션 keep 탭 → 계위 라벨 → 섹션계위 → ID.

    pipeline.run(post_process=False) 또는 OCR 추출 결과 reqs 를 받아 동적 writer 입력으로 마감.
    (캐시 reqs 빠른 반복에도 사용)
    """
    n0 = len(reqs)
    reqs = sorted(reqs, key=_page_key)  # 페이지순(안정정렬 — 같은 페이지 내 원순서 유지)

    # 탭 = 문서 섹션 헤딩(가/나/다/라/마/별첨). LLM 은 keep(요구영역 vs boilerplate)만 판정
    # (merge/도메인작명 안 함=충실·결정론). boilerplate 섹션만 drop, 기타사항·지원방안 등은 유지.
    reqs = assign_section_tabs_sync(reqs)
    steps.append(f"섹션 keep(탭=섹션헤딩): {n0}→{len(reqs)}행, {len({r.tab for r in reqs})}탭 (행단위 드롭 없음)")

    reqs = assign_hierarchy_labels_sync(reqs)  # 최종 탭 맥락에서 계위 라벨
    assign_section_levels(reqs, use_section_levels=True)
    assign_ids(reqs)  # 최종 탭/분류 기준 요구사항 ID (pipeline 후처리 스킵분 보강)
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
