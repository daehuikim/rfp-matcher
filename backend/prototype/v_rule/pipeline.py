"""v_rule 오케스트레이터 — PDF → 변환 → 표후처리 → (계층 walk) → 조견표 룰 → Excel.

현재 카드 분할은 **HTML 계층(heading) 기반**(Section→탭)으로 부트스트랩. TOC/anchor 카드 분할은
toc_card 모듈로 후속 결합 예정(스펙 3단계). 본 파이프라인이 스펙 1·2·4·5단계를 end-to-end 수행.
"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from .convert import convert_pdf
from .db import HeadingStack, Row, heading_label, heading_level
from .excel import write_excel
from .rules import body_to_rows, table_to_rows
from .table_post import Table, _detect_header, _expand_spans, _heading_before, \
    _trim_edge_empty_cols, _join_wrapped, _fill_down


def _postprocess_table(grid: list[list[str]], hint: str) -> Table:
    t = Table(rows=grid, section_hint=hint, header_row=_detect_header(grid))
    t.rows = _trim_edge_empty_cols(t.rows)
    t.header_row = _detect_header(t.rows)
    t.rows = _join_wrapped(t.rows, t.header_row)
    t.rows = _fill_down(t.rows, t.header_row)
    return t


def _walk(html: str) -> list[Row]:
    """HTML 문서 순서로 walk — heading→스택, 다단표→표룰, 1단표/문단/리스트→본문룰."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    stack = HeadingStack()
    rows: list[Row] = []
    body_buf: list[str] = []

    def flush_body():
        nonlocal body_buf
        if body_buf:
            rows.extend(body_to_rows(body_buf, stack))
            body_buf = []

    # NOTE: 다페이지 표 병합(merge_fragments)은 walk 와 분리돼 후속 결합 예정(현재 표 개별 처리).
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"]):
        if el.name != "table" and el.find_parent("table"):
            continue
        if el.name == "table":
            if el.find_parent("table") is not None:
                continue
            flush_body()
            grid = _expand_spans(el)
            if not any(c.strip() for row in grid for c in row):
                continue
            t = _postprocess_table(grid, _heading_before(soup, el))
            if t.ncols <= 1:
                # opendataloader 가 본문을 1칸 표로 감쌈 → 본문룰
                for row in t.rows:
                    txt = " ".join(c for c in row if c).strip()
                    if txt:
                        rows.extend(body_to_rows([txt], stack))
            else:
                rows.extend(table_to_rows(t, stack))
            continue
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if not txt:
            continue
        lvl = heading_level(txt)
        if lvl is not None:
            flush_body()
            stack.push(lvl, heading_label(txt))
        else:
            body_buf.append(txt)
    flush_body()
    return rows


def run(pdf_path: str | Path, workdir: str | Path, out_xlsx: str | Path) -> dict:
    """PDF → Excel(v_rule). 반환: 산출물 경로·통계."""
    conv = convert_pdf(pdf_path, workdir)
    if "html" not in conv:
        raise RuntimeError("OpenDataLoader HTML 변환 실패")
    html = conv["html"].read_text(encoding="utf-8", errors="replace")
    rows = _walk(html)
    stats = write_excel(rows, out_xlsx)
    stats.update({"convert": {k: str(v) for k, v in conv.items()}, "out": str(out_xlsx)})
    return stats
