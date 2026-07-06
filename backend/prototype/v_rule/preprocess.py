"""HTML 후처리(전처리) 스테이지 — 중구난방 변환물을 사람이 보듯 정리. 관찰가능(trace 반환).

수행: (1) 빈 블록 제거 (2) hwp5html/opendataloader 의 '제목+본문 병합 <p>' 및 p/td 중복
정리 — 인접 블록이 같은 마커+제목접두를 가지면 **짧은(깨끗한) 헤딩만 남김**. 하드코딩 없음.
반환: (clean_blocks, trace) — trace 로 전/후 카운트를 로그에 남긴다.
"""
from __future__ import annotations

import re

from .cards import Block, iter_blocks, marker_level, strip_marker


def split_table_headings(blocks: list[Block]) -> list[Block]:
    """표 블록 안의 '헤딩 행'(마커+짧은제목·sparse)을 텍스트 블록으로 분리.

    hwp5html 은 헤딩('Ⅰ | | 사업개요')·목차까지 표 행으로 만든다 → 표 안 마커를 카드로 살린다.
    표 블록에만 적용(텍스트 블록 불변) + 보수적 판정이라 진짜 데이터 표는 그대로 유지.
    """
    out: list[Block] = []
    for b in blocks:
        if b.kind != "table":
            out.append(b); continue
        buf: list[list[str]] = []
        for row in b.grid:
            ne = [c.strip() for c in row if c and c.strip()]
            joined = re.sub(r"\s+", " ", " ".join(ne))   # 셀 내 개행 무시(헤딩 판정은 1줄 기준)
            lvl = marker_level(joined)
            # 레벨 0~3만 헤딩(章·절·항), 4~5(1)/❍/- 등)는 원래 '내용'(card_extract.py 동일 컨벤션).
            # 이걸 안 가리면 "26) IVR 단계에서..." 같은 완결 요구사항 문장(레벨4 마커)까지
            # 표에서 뽑혀 나가 build_units 의 표-격리 로직을 건너뛰고 콜래터럴 드롭 위험에 노출됨.
            if ne and len(ne) <= 3 and len(joined) <= 45 and lvl is not None and lvl <= 3:
                if buf:
                    out.append(Block(kind="table", grid=buf)); buf = []
                out.append(Block(kind="text", text=joined))
            else:
                buf.append(row)
        if buf:
            out.append(Block(kind="table", grid=buf))
    return out


def _key(b: Block) -> tuple[str, str]:
    parts = b.text.split()
    return (parts[0] if parts else "", strip_marker(b.text)[:12])


def preprocess(html: str) -> tuple[list[Block], dict]:
    raw = iter_blocks(html)   # p/li/h/table 문서순 + 빈/직전중복 1차 제거
    raw = split_table_headings(raw)   # 표 안 헤딩행 → 텍스트(카드 마커 살리기)
    out: list[Block] = []
    trace = {"raw_blocks": len(raw), "tables": 0, "texts": 0,
             "heading_dups_collapsed": 0, "text_before": 0}
    trace["text_before"] = sum(1 for b in raw if b.kind == "text")
    for b in raw:
        if b.kind == "table":
            out.append(b); trace["tables"] += 1; continue
        if (out and out[-1].kind == "text" and _key(b)[0] and _key(out[-1]) == _key(b)):
            # 같은 마커+제목접두 → 짧은(깨끗한 헤딩) 유지, 긴(제목+본문 병합) 버림.
            # htag 는 어느 쪽에 붙어있든 보존 — DOCX/HWP 순수제목 헤딩 판정(build_units의
            # tag_lvl 폴백)이 병합 과정에서 무력화되지 않게.
            htag = b.htag or out[-1].htag
            if len(b.text) < len(out[-1].text):
                out[-1] = b
            out[-1].htag = out[-1].htag or htag
            trace["heading_dups_collapsed"] += 1
            continue
        out.append(b); trace["texts"] += 1
    trace["clean_blocks"] = len(out)
    return out, trace


def blocks_to_text(blocks: list[Block]) -> str:
    """전처리된 블록을 사람이 읽는 순서 텍스트로(로그용) — 마커 계위 들여쓰기 표시."""
    lines = []
    for b in blocks:
        if b.kind == "table":
            lines.append(f"    [표 {len(b.grid)}행 x {max((len(r) for r in b.grid), default=0)}열]")
            for r in b.grid[:3]:
                lines.append("      | " + " | ".join(c[:20] for c in r))
            if len(b.grid) > 3:
                lines.append(f"      … +{len(b.grid)-3}행")
        else:
            lvl = marker_level(b.text)
            indent = "  " * (lvl if lvl is not None else 4)
            lines.append(f"{indent}{b.text[:100]}")
    return "\n".join(lines)
