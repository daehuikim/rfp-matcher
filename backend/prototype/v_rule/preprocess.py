"""HTML 후처리(전처리) 스테이지 — 중구난방 변환물을 사람이 보듯 정리. 관찰가능(trace 반환).

수행: (1) 빈 블록 제거 (2) hwp5html/opendataloader 의 '제목+본문 병합 <p>' 및 p/td 중복
정리 — 인접 블록이 같은 마커+제목접두를 가지면 **짧은(깨끗한) 헤딩만 남김**. 하드코딩 없음.
반환: (clean_blocks, trace) — trace 로 전/후 카운트를 로그에 남긴다.
"""
from __future__ import annotations

from .cards import Block, iter_blocks, marker_level, strip_marker


def _key(b: Block) -> tuple[str, str]:
    parts = b.text.split()
    return (parts[0] if parts else "", strip_marker(b.text)[:12])


def preprocess(html: str) -> tuple[list[Block], dict]:
    raw = iter_blocks(html)   # p/li/h/table 문서순 + 빈/직전중복 1차 제거
    out: list[Block] = []
    trace = {"raw_blocks": len(raw), "tables": 0, "texts": 0,
             "heading_dups_collapsed": 0, "text_before": 0}
    trace["text_before"] = sum(1 for b in raw if b.kind == "text")
    for b in raw:
        if b.kind == "table":
            out.append(b); trace["tables"] += 1; continue
        if (out and out[-1].kind == "text" and _key(b)[0] and _key(out[-1]) == _key(b)):
            # 같은 마커+제목접두 → 짧은(깨끗한 헤딩) 유지, 긴(제목+본문 병합) 버림
            if len(b.text) < len(out[-1].text):
                out[-1] = b
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
