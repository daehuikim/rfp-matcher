"""rfpmatch Row → v2 Req 어댑터 — 앱 다운스트림(_v2_to_requirement·AI매칭·export) 호환.

rfpmatch 파이프라인의 섹션별 요구사항표(dict 행)를 앱의 v2 Req 포맷으로 변환해, FE
업로드→rfpmatch 추출→AI→export 풀 파이프라인을 v2/v_rule과 동일하게 태운다.
v_rule/adapter.py와 동일한 역할(엔진별 어댑터 컨벤션).
"""

from __future__ import annotations

from pathlib import Path

from prototype.v2.extract import Req

from .pipeline import run


def _section_tables_to_v2_reqs(section_tables: dict[str, list[dict]], doc_name: str) -> list[Req]:
    """섹션별 요구사항표 → Req 리스트. rid는 엔진이 이미 계산한 "요구사항 ID"를 그대로 사용."""
    reqs: list[Req] = []
    for section_name, rows in section_tables.items():
        for row in rows:
            section_path = " > ".join(
                x
                for x in [str(row.get("Part") or ""), str(row.get("Section") or section_name)]
                if x
            )
            page = row.get("페이지")
            reqs.append(
                Req(
                    doc=doc_name,
                    table_id=-1,
                    page=page if isinstance(page, int) else None,
                    rid=str(row.get("요구사항 ID") or ""),
                    top=str(row.get("항목명") or ""),
                    mid=str(row.get("요구사항") or ""),
                    detail=str(row.get("상세요건") or ""),
                    section_path=section_path,
                    tab=section_name,
                    source=str(row.get("생성 출처") or ""),
                    levels=[x for x in [row.get("항목명"), row.get("요구사항")] if x],
                    level_names=["항목명", "요구사항"],
                )
            )
    return reqs


def run_rfpmatch_reqs(
    src_path: str | Path,
    workdir: str | Path,
    *,
    use_llm: bool = True,
) -> list[Req]:
    """문서(PDF/HWP/HWPX/DOCX) → rfpmatch 파이프라인(TOC/섹션/카드/요구사항표 자동화) → Req 리스트.

    앱 추출엔진 진입점(v_rule.adapter.run_v_rule_reqs와 동일한 역할).
    """
    src = Path(src_path)
    result = run(src, workdir, use_llm=use_llm)
    return _section_tables_to_v2_reqs(result["section_tables"], src.stem)
