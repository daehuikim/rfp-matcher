"""v_rule Row → v2 Req 어댑터 — 앱 다운스트림(_v2_to_requirement·AI매칭·export) 호환.

v_rule 의 룰 추출 결과를 앱의 v2 Req 포맷으로 변환해, FE 업로드→v_rule 추출→AI→예쁜 export
풀 파이프라인을 v2 와 동일하게 태운다(공정 비교).
"""
from __future__ import annotations

import re
from pathlib import Path

from prototype.v2.extract import Req
from prototype.v2.ids import assign_ids

from .cards import IMG_MARKER_RE
from .convert import convert_any
from .pipeline import _walk


def _extract_cell_images(detail: str, img_dir: Path) -> tuple[list[str], str]:
    """상세문 안 "[표이미지:파일명]" 마커를 뽑아 실제 파일 경로 목록으로, 텍스트에선 제거.

    표를 이미지로 렌더링해 셀에 넣는다는 요청 반영 — 다운스트림(gemma 분류·길이체크)에는
    짧은 마커 텍스트로만 보였다가, 여기서 Req.detail_images 로 옮겨 엑셀 writer 가
    실제 이미지로 삽입한다(기존 v2 컨벤션 재사용). 파일이 없으면(렌더 실패) 마커만 지운다.
    """
    names = IMG_MARKER_RE.findall(detail or "")
    if not names:
        return [], detail or ""
    paths = [str(img_dir / n) for n in names if (img_dir / n).is_file()]
    stripped = IMG_MARKER_RE.sub("", detail or "").strip()
    stripped = re.sub(r"\n{2,}", "\n", stripped).strip()
    if not stripped and paths:
        stripped = "[표]"   # v2 컨벤션: detail=="[표]" → 이미지가 내용을 담는 캡션
    return paths, stripped


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
    from .card_extract import build_units, _judge_keep, rows_from_units, detect_canonical_categories

    src = Path(src_path)
    conv = convert_any(src, workdir)
    if "html" not in conv:
        return []
    html_path = conv["html"]
    html = html_path.read_text(encoding="utf-8", errors="replace")
    img_dir = Path(workdir) / "cell_images"
    blocks, _tr = preprocess(html, img_dir=img_dir, base_dir=html_path.parent)
    units = build_units(blocks=blocks)
    keep_map = _judge_keep(units) if keep else {i: True for i in range(len(units))}
    # 문서 자체 선언 분류(총괄표/R-헤딩: SFR/ECR/DAR…) 감지 → 조견표 탭의 정본으로 사용.
    # 원문 텍스트도 함께 — 표 추출이 총괄표의 ID부여규칙/건수 열을 떨어뜨려도 선언 보존.
    import re as _re
    canon = detect_canonical_categories(units, raw_text=_re.sub(r"<[^>]+>", " ", html))
    if canon and src.suffix.lower() == ".pdf":
        # 러닝헤더 섹션 라벨은 opendataloader 가 제거하므로 원본 PDF 에서 직접 수집
        from .card_extract import merge_pdf_label_pages
        merge_pdf_label_pages(canon, src)
    rows = rows_from_units(units, keep_map, canonical=canon)
    reqs: list[Req] = []
    for r in rows:
        images, detail = _extract_cell_images(r["detail"], img_dir)
        reqs.append(Req(
            doc=src.stem, table_id=r.get("table_index"), page=r.get("page"), rid=r["code"],
            top=r["name"], mid=r["level"], detail=detail, tab=r["tab"],
            section_path=r["level"], levels=[r["name"], r["level"]],
            level_names=["요구사항명", "계위"], detail_images=images,
        ))
    return reqs
