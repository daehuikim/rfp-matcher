"""v_rule Row → v2 Req 어댑터 — 앱 다운스트림(_v2_to_requirement·AI매칭·export) 호환.

v_rule 의 룰 추출 결과를 앱의 v2 Req 포맷으로 변환해, FE 업로드→v_rule 추출→AI→예쁜 export
풀 파이프라인을 v2 와 동일하게 태운다(공정 비교).
"""
from __future__ import annotations

from pathlib import Path

from prototype.v2.extract import Req
from prototype.v2.ids import assign_ids

from .convert import convert_any
from .pipeline import _walk


def rows_to_v2reqs(rows: list, doc_name: str) -> list[Req]:
    """v_rule Row 리스트 → v2 Req 리스트(tab=Section, top=항목명, mid=요구사항, detail=상세요건)."""
    reqs: list[Req] = []
    for r in rows:
        sp = " > ".join(x for x in [r.part, r.section, r.subject, r.sub_subject] if x)
        src = "; ".join(x for x in [f"p.{r.page}" if r.page else "", f"참조:{r.ref}" if r.ref else ""] if x)
        reqs.append(Req(
            doc=doc_name,
            table_id=r.table_id if r.table_id is not None else -1,
            page=r.page,
            top=r.item or "",
            mid=r.requirement or "",
            detail=r.detail or "",
            section_path=sp,
            tab=(r.section or "요구사항"),
            source=src,
            levels=[x for x in [r.item, r.requirement] if x],
            level_names=["항목명", "요구사항"],
        ))
    assign_ids(reqs)  # 탭 기반 rid — 한 탭 = 한 접두사(앱 규칙)
    return reqs


def run_v_rule_reqs(src_path: str | Path, workdir: str | Path, *, keep: bool = True) -> list[Req]:
    """문서(PDF/HWP/HWPX) → **카드 추출**(전처리→가·나 카드→gemma keep) → v2 Req 리스트.

    앱 추출엔진 진입점. keep=True 면 gemma 가 비요구 카드(표지·목차·현황 등) 필터.
    매핑: 요구사항ID=code(탭별), 요구사항명=name(카드제목), 계위=level, 상세내용=detail.
    """
    from .preprocess import preprocess
    from .card_extract import build_units, _judge_keep, rows_from_units

    src = Path(src_path)
    conv = convert_any(src, workdir)
    if "html" not in conv:
        return []
    html = conv["html"].read_text(encoding="utf-8", errors="replace")
    blocks, _tr = preprocess(html)
    units = build_units(blocks=blocks)
    keep_map = _judge_keep(units) if keep else {i: True for i in range(len(units))}
    rows = rows_from_units(units, keep_map)
    reqs: list[Req] = []
    for r in rows:
        reqs.append(Req(
            doc=src.stem, table_id=-1, page=None, rid=r["code"],
            top=r["name"], mid=r["level"], detail=r["detail"], tab=r["tab"],
            section_path=r["level"], levels=[r["name"], r["level"]],
            level_names=["요구사항명", "계위"],
        ))
    return reqs
