"""
금융 RFP HTML 추출 (문서·은행 무관).

- 구간 분류: FinancialZoneClassifier (heading + 미리보기 → LLM)
- 리스트 제목/본문: FinancialListHeadingSplitter (LLM)
- 상세 셀 분해: DetailCellSplitter (표마다 LLM 스키마)
- Excel: 요건구분 + 상세내용 + 출처 (financial_excel_writer)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from prototype.v2.classify import classify, detect_header_row, is_requirement_table
from prototype.v2.extract import Req, atomize_detail, format_source, level_table
from prototype.v2.grid import _direct_rows
from prototype.v2.grid import _html_table_to_grid
from prototype.v2.overview import build_overview_from_html_sync, build_overview_sync
from prototype.v2.tab_naming import is_bad_tab_name, sanitize_hierarchy_labels
from prototype.v2.text import norm, norm_lines, sig

from .detail_cell_split import DetailCellSplitter, make_splitter
from .financial_list_llm import FinancialListHeadingSplitter, make_list_heading_splitter
from .financial_zone_llm import FinancialZoneClassifier, ZoneState, ZoneTask, make_zone_classifier
from .financial_content import (
    is_appendix_part,
    is_context_section,
    is_reference_section,
    is_toc_line,
    table_passes_content_gate,
    text_reads_as_inventory,
    text_reads_as_requirement,
    text_reads_as_table_criterion,
)
from .financial_heading import (
    heading_level_from_number,
    is_numbered_section_title,
    is_numbered_detail_item,
    is_subgroup_label,
    is_valid_req_group_label,
    lowest_category_label,
    parse_numbered_section,
    preserve_group_label,
    preserve_tab_name,
    split_title_body,
    tab_from_section_path,
)

_DASH_BULLET_LINE = re.compile(r"^-\s+")

_BULLET_START = re.compile(r"^[•·▪◦∙\-–—]")
_HANGUL = re.compile(r"[가-힣]")
_AGREEMENT_NOISE = re.compile(r"제안\s*\(불\)참여|감사드립니다|진심으로", re.I)


def format_financial_source(
    *,
    page: int | None = None,
    table_id: int = -1,
) -> str:
    """출처 — p.N · 표#N|리스트 (계위는 카테고리 칼럼)."""
    return format_source(table_id=table_id, page=page, section="")


@dataclass
class _Ctx:
    headings: dict[int, str] = field(default_factory=dict)
    table_id: int = 0
    zone: ZoneState = field(default_factory=ZoneState)
    zones: FinancialZoneClassifier | None = None
    last_detail_group: str = ""
    list_group: str = ""
    last_page: int | None = None
    last_req_table_id: int = -1
    last_req_page: int | None = None
    splitter: DetailCellSplitter | None = None
    list_splitter: FinancialListHeadingSplitter | None = None
    heading_pages: dict[str, int] = field(default_factory=dict)
    doc_seq: int = 0
    prev_table_roles: dict[str, int] | None = None
    prev_table_ncols: int | None = None

    def section_path(self) -> str:
        return " > ".join(self.headings[d] for d in sorted(self.headings))

    def tab(self, override: str = "") -> str:
        sp = self.section_path()
        merged = tab_from_section_path(sp, self.zone.tab)
        if merged and merged != "요구사항":
            return merged
        if override:
            name = preserve_tab_name(override)
            if name and name != "요구사항" and not is_bad_tab_name(name):
                return name
        return "요구사항"

    def req_group(self, explicit: str = "") -> str:
        """요건구분 — 표 라벨 > 리스트 제목 > LLM subgroup > 최하위 카테고리."""
        for cand in (
            explicit,
            self.list_group,
            self.last_detail_group,
            self.zone.subgroup,
        ):
            if not cand:
                continue
            if is_context_section(cand) or is_reference_section(cand):
                continue
            if is_valid_req_group_label(cand):
                return cand
        return lowest_category_label(self.section_path())


def _in_appendix(ctx: _Ctx) -> bool:
    return any(is_appendix_part(p) for p in ctx.section_path().split(" > ") if norm(p))


def _in_reference(ctx: _Ctx) -> bool:
    return any(is_reference_section(p) for p in ctx.section_path().split(" > ") if norm(p))


def _detail_passes_gate(piece: str, *, table_id: int, relax_gate: bool = False) -> bool:
    if relax_gate:
        return True
    if table_id >= 0:
        return text_reads_as_table_criterion(piece)
    return text_reads_as_requirement(piece)


def _split_dash_bullet_cell(text: str) -> list[str]:
    """표 셀 '- ' 불릿 다중 행 — QA 조견표 입도."""
    text = norm_lines(text)
    if not text:
        return []
    # grid flatten: "문장 - 불릿 - 불릿"
    if text.count(" - ") >= 2:
        parts = [p.strip() for p in re.split(r"\s+-\s+", text) if p.strip()]
        if len(parts) >= 3:
            out = [f"{parts[0]} - {parts[1]}"]
            out.extend(f"- {p}" if not p.startswith("-") else p for p in parts[2:])
            return out
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    dash_n = sum(1 for ln in lines if _DASH_BULLET_LINE.match(ln))
    if dash_n < 1 or len(lines) <= 1:
        return [text]
    pieces: list[str] = []
    i = 0
    while i < len(lines):
        if _DASH_BULLET_LINE.match(lines[i]):
            pieces.append(lines[i])
            i += 1
            continue
        preamble: list[str] = []
        while i < len(lines) and not _DASH_BULLET_LINE.match(lines[i]):
            preamble.append(lines[i])
            i += 1
        if i < len(lines) and _DASH_BULLET_LINE.match(lines[i]):
            pieces.append("\n".join(preamble + [lines[i]]))
            i += 1
        elif preamble:
            pieces.append("\n".join(preamble))
    return pieces or [text]


def _expand_detail_pieces(
    ctx: _Ctx,
    detail: str,
    *,
    table_id: int,
    group: str,
) -> list[str]:
    detail = norm_lines(detail)
    if not detail:
        return []
    if table_id < 0:
        return [detail]
    dash = _split_dash_bullet_cell(detail)
    if len(dash) > 1:
        return dash
    if ctx.zone.split_cells and ctx.splitter:
        split = ctx.splitter.split_cell(detail, table_id=table_id, group=group)
        if len(split) > 1:
            return split
    parts = atomize_detail(detail, "fine")
    return parts if parts else [detail]


def _effective_table_id(ctx: _Ctx, table_id: int, group: str) -> int:
    """표 셀 직후 DOM 밖 불릿 — 같은 요건구분이면 표 출처 유지."""
    if table_id >= 0:
        return table_id
    if (
        ctx.last_req_table_id >= 0
        and group
        and group == ctx.last_detail_group
    ):
        return ctx.last_req_table_id
    return table_id


def _effective_page(ctx: _Ctx, page: int | None, table_id: int) -> int | None:
    if table_id >= 0 and page is not None:
        return page
    if table_id >= 0 and ctx.last_req_page is not None:
        return ctx.last_req_page
    return page if page is not None else ctx.last_page


def _filter_appendix_reqs(reqs: list[Req]) -> list[Req]:
    out: list[Req] = []
    for r in reqs:
        if any(is_appendix_part(p) for p in (r.section_path or "").split(" > ") if norm(p)):
            continue
        if any(is_context_section(p) for p in (r.section_path or "").split(" > ") if norm(p)):
            continue
        if any(is_reference_section(p) for p in (r.section_path or "").split(" > ") if norm(p)):
            continue
        if is_appendix_part(r.tab) or is_context_section(r.tab) or is_reference_section(r.tab):
            continue
        out.append(r)
    return out


def _heading_preview(el: Tag) -> str:
    parts: list[str] = []
    for sib in el.next_siblings:
        if not isinstance(sib, Tag):
            continue
        if sib.name in ("h3", "h4", "h5", "h6"):
            break
        if sib.name in ("p", "ul", "table", "ol"):
            parts.append(norm(sib.get_text(" ", strip=True))[:240])
        if sum(len(p) for p in parts) > 500:
            break
    return "\n".join(parts)[:500]


def _update_heading(ctx: _Ctx, level: int, text: str, *, preview: str = "") -> None:
    t = norm(text)
    if not t or is_toc_line(t):
        return
    for d in list(ctx.headings):
        if d >= level:
            del ctx.headings[d]
    ctx.headings[level] = t
    ctx.list_group = ""

    prev_tab = ctx.zone.tab
    if ctx.zones:
        ctx.zone = ctx.zones.classify(
            heading=t,
            level=level,
            section_path=ctx.section_path(),
            preview=preview,
        )
    elif level >= 4:
        sub = preserve_group_label(t)
        if sub and not is_bad_tab_name(sub):
            ctx.zone = ZoneState(True, ctx.zone.tab or "요구사항", sub, ctx.zone.split_cells)
    if _in_appendix(ctx) or is_appendix_part(t) or is_context_section(t) or is_reference_section(t):
        ctx.zone = ZoneState(False, "", "", False)
    if ctx.zone.tab != prev_tab:
        ctx.last_detail_group = ""
        ctx.last_req_table_id = -1
        ctx.prev_table_roles = None
        ctx.prev_table_ncols = None
    hp = _page_for_heading(t, ctx.heading_pages)
    if hp is not None:
        ctx.last_page = hp


def _inline_preview(el: Tag) -> str:
    parts: list[str] = []
    for sib in el.next_siblings:
        if not isinstance(sib, Tag):
            continue
        if sib.name in ("h3", "h4", "h5", "h6", "p") and sib is not el:
            t = norm(sib.get_text(" ", strip=True))
            if t and is_numbered_section_title(t):
                break
        if sib.name in ("p", "ul", "table", "ol"):
            parts.append(norm(sib.get_text(" ", strip=True))[:240])
        if sum(len(p) for p in parts) > 500:
            break
    return "\n".join(parts)[:500]


def _apply_numbered_section(
    ctx: _Ctx,
    text: str,
    *,
    preview: str = "",
) -> bool:
    """heading 태그 없이 `<p>`/`<li>`에만 있는 절 제목(2.7.4 …) 구간 갱신."""
    if not is_numbered_section_title(text):
        return False
    parsed = parse_numbered_section(text)
    if not parsed:
        return False
    num, body = parsed
    full = preserve_group_label(f"{num}. {body}")
    level = heading_level_from_number(num)
    for d in list(ctx.headings):
        if d >= level:
            del ctx.headings[d]
    ctx.headings[level] = full
    if is_valid_req_group_label(full):
        ctx.list_group = full
    prev_tab = ctx.zone.tab
    if ctx.zones:
        ctx.zone = ctx.zones.classify(
            heading=full,
            level=level,
            section_path=ctx.section_path(),
            preview=preview or body[:500],
        )
    if ctx.zone.tab != prev_tab:
        ctx.last_detail_group = ""
    hp = _page_for_heading(full, ctx.heading_pages)
    if hp is not None:
        ctx.last_page = hp
    return True


def _collecting(ctx: _Ctx) -> bool:
    return (
        bool(ctx.headings)
        and ctx.zone.collect
        and not _in_appendix(ctx)
        and not _in_reference(ctx)
    )


def _reject_detail(detail: str) -> bool:
    if len(sig(detail)) < 2 or is_toc_line(detail):
        return True
    if _AGREEMENT_NOISE.search(detail):
        return True
    return text_reads_as_inventory(detail)


def _cell_detail_text(cell: Tag) -> str:
    """`<p>` 경계를 살려 ①·• 계층을 보존."""
    lines: list[str] = []
    for el in cell.find_all("p"):
        t = norm(el.get_text(" ", strip=True))
        if t:
            lines.append(t)
    if lines:
        return "\n".join(lines)
    return norm(cell.get_text("\n", strip=True))


def _is_header_row(texts: list[str]) -> bool:
    joined = norm(" ".join(texts))
    return "요건" in joined and "구분" in joined and "상세" in joined


def _looks_like_label(text: str) -> bool:
    return is_valid_req_group_label(text)


def _row_label_detail(tr: Tag) -> tuple[str, str]:
    cells = [c for c in tr.find_all(["td", "th"], recursive=False)]
    texts = [_cell_detail_text(c) if c.name == "td" else norm(c.get_text("\n", strip=True)) for c in cells]
    nonempty = [t for t in texts if norm(t)]
    if not nonempty or _is_header_row(nonempty):
        return "", ""

    label = ""
    detail_parts: list[str] = []
    for t in nonempty:
        if not label and _looks_like_label(t) and not detail_parts:
            label = t
        else:
            detail_parts.append(t)
    if label and not detail_parts:
        return "", label
    return label, "\n".join(detail_parts)


def _merge_continuation(
    out: list[Req],
    group: str,
    detail: str,
    *,
    ctx: _Ctx,
    table_id: int,
) -> bool:
    """불릿만 이어지는 행을 직전 상세 셀에 병합."""
    detail = norm_lines(detail)
    if not detail or not out or not ctx.splitter:
        return False
    if not ctx.splitter.is_continuation_only(detail, table_id=table_id):
        return False
    prev = out[-1]
    effective = group or prev.top
    if not effective or prev.top != effective:
        return False
    prev.detail = norm_lines(f"{prev.detail}\n{detail}")
    return True


def _emit_detail_piece(
    out: list[Req],
    *,
    doc_name: str,
    ctx: _Ctx,
    group: str,
    piece: str,
    table_id: int,
    page: int | None,
) -> None:
    piece = norm_lines(piece)
    if not piece or _reject_detail(piece) or not _detail_passes_gate(piece, table_id=table_id):
        return
    eff_tid = _effective_table_id(ctx, table_id, group)
    eff_page = _effective_page(ctx, page, eff_tid)
    if table_id >= 0:
        ctx.last_req_table_id = table_id
        ctx.last_req_page = eff_page
    sp = ctx.section_path()
    out.append(
        Req(
            doc=doc_name,
            table_id=eff_tid,
            page=eff_page,
            top=group,
            mid="",
            detail=piece,
            section_path=sp,
            tab=ctx.tab(),
            source=format_financial_source(page=eff_page, table_id=eff_tid),
        )
    )
    ctx.doc_seq += 1


def _split_detail_pieces(ctx: _Ctx, detail: str, *, table_id: int, group: str) -> list[str]:
    return _expand_detail_pieces(ctx, detail, table_id=table_id, group=group)


def _push_detail_req(
    out: list[Req],
    *,
    doc_name: str,
    ctx: _Ctx,
    group: str,
    detail: str,
    table_id: int,
    page: int | None,
) -> None:
    detail = norm_lines(detail)
    if not detail or _reject_detail(detail) or not _detail_passes_gate(detail, table_id=table_id):
        return
    pieces = _split_detail_pieces(ctx, detail, table_id=table_id, group=group)
    for piece in pieces:
        if (
            not re.match(r"^[①②③④⑤⑥⑦⑧⑨⑩]", piece)
            and out
            and out[-1].top == group
        ):
            out[-1].detail = norm_lines(f"{out[-1].detail}\n{piece}")
            continue
        _emit_detail_piece(
            out,
            doc_name=doc_name,
            ctx=ctx,
            group=group,
            piece=piece,
            table_id=table_id,
            page=page,
        )


def _append_req(
    out: list[Req],
    *,
    doc_name: str,
    ctx: _Ctx,
    detail: str,
    group: str = "",
    tab: str = "",
    table_id: int = -1,
    relax_gate: bool = False,
) -> None:
    if not _collecting(ctx):
        return
    detail = norm(detail)
    if _reject_detail(detail):
        return
    if not relax_gate and not _detail_passes_gate(detail, table_id=table_id):
        return
    sheet = ctx.tab(tab)
    grp = ctx.req_group(group)
    if grp:
        ctx.last_detail_group = grp
    carry_tid = _effective_table_id(ctx, table_id, grp)
    split_tid = carry_tid if carry_tid >= 0 else table_id
    if table_id < 0:
        pieces = [detail]
    else:
        pieces = _expand_detail_pieces(ctx, detail, table_id=split_tid, group=grp)
    for piece in pieces:
        if _reject_detail(piece):
            continue
        if not relax_gate and not _detail_passes_gate(piece, table_id=table_id):
            continue
        eff_tid = _effective_table_id(ctx, table_id, grp)
        eff_page = _effective_page(ctx, ctx.last_page, eff_tid)
        if table_id >= 0:
            ctx.last_req_table_id = table_id
            ctx.last_req_page = eff_page
        sp = ctx.section_path()
        out.append(
            Req(
                doc=doc_name,
                table_id=eff_tid,
                page=eff_page,
                top=grp,
                mid="",
                detail=piece,
                section_path=sp,
                tab=sheet,
                source=format_financial_source(page=eff_page, table_id=eff_tid),
            )
        )
        ctx.doc_seq += 1


def _is_youngun_sangse_table(grid) -> bool:
    if not grid.cells:
        return False
    hdr = norm(" ".join(grid.cells[0]))
    return "요건" in hdr and "구분" in hdr and "상세" in hdr


def _is_youngun_sangse_html_table(el: Tag) -> bool:
    rows = _direct_rows(el)
    if not rows:
        return False
    texts = [
        norm(c.get_text(" ", strip=True))
        for c in rows[0].find_all(["td", "th"], recursive=False)
    ]
    joined = " ".join(texts)
    return "요건" in joined and "구분" in joined and "상세" in joined


def _process_detail_req_table(
    el: Tag,
    ctx: _Ctx,
    doc_name: str,
    out: list[Req],
    *,
    page: int | None,
) -> None:
    """상세 요구사항 — DOM 순서 유지, 연속 표, LLM 셀 분해."""
    table_id = ctx.table_id
    for tr in _direct_rows(el):
        label, detail = _row_label_detail(tr)
        if not detail and not label:
            continue
        if label:
            label = preserve_group_label(label)
            if is_valid_req_group_label(label):
                ctx.last_detail_group = label
            else:
                detail = norm_lines(f"{label}\n{detail}") if detail else label
                label = ""
        group = ctx.req_group(label) if label else ctx.req_group()
        if not detail:
            if group:
                ctx.last_detail_group = group
            continue
        if not group:
            continue
        if label and group:
            ctx.last_detail_group = group
        if not label and _merge_continuation(
            out, group, detail, ctx=ctx, table_id=table_id
        ):
            continue
        _push_detail_req(
            out,
            doc_name=doc_name,
            ctx=ctx,
            group=group,
            detail=detail,
            table_id=table_id,
            page=page,
        )


def _process_table(
    el: Tag,
    ctx: _Ctx,
    doc_name: str,
    out: list[Req],
    *,
    page: int | None = None,
) -> None:
    if not _collecting(ctx):
        return
    if _collecting(ctx) and _is_youngun_sangse_html_table(el):
        if page is None:
            page = ctx.last_page
        elif page is not None:
            ctx.last_page = page
        _process_detail_req_table(el, ctx, doc_name, out, page=page)
        ctx.table_id += 1
        return
    grid = _html_table_to_grid(el, ctx.table_id)
    ctx.table_id += 1
    if _is_youngun_sangse_table(grid):
        ctx.prev_table_roles = None
        ctx.prev_table_ncols = None
        return
    if not table_passes_content_gate(grid, ctx.headings):
        ctx.prev_table_roles = None
        ctx.prev_table_ncols = None
        return
    header_row = detect_header_row(grid)
    roles: dict[str, int] | None = None
    if header_row >= 0:
        cand = classify(grid, header_row)
        if is_requirement_table(grid, cand, header_row):
            roles = cand
    elif (
        ctx.prev_table_roles is not None
        and ctx.prev_table_ncols == grid.ncols
    ):
        roles = ctx.prev_table_roles
    else:
        cand = classify(grid, header_row)
        if is_requirement_table(grid, cand, header_row):
            roles = cand
    if roles is None or "detail" not in roles:
        ctx.prev_table_roles = None
        ctx.prev_table_ncols = None
        return
    sec = ctx.headings.get(6, "") or ctx.headings.get(4, "") or ctx.section_path()
    rows = level_table(doc_name, grid, roles, header_row, sec, "macro")
    ctx.prev_table_roles = roles
    ctx.prev_table_ncols = grid.ncols
    sheet = ctx.tab()
    sp = ctx.section_path()
    eff_page = page if page is not None else ctx.last_page
    for r in rows:
        if _reject_detail(r.detail):
            continue
        grp = ctx.req_group(r.top or r.mid)
        if grp:
            ctx.last_detail_group = grp
        pieces = _expand_detail_pieces(
            ctx, r.detail, table_id=r.table_id, group=grp or r.top or r.mid
        )
        for piece in pieces:
            if _reject_detail(piece) or not text_reads_as_table_criterion(piece):
                continue
            out.append(
                Req(
                    doc=doc_name,
                    table_id=r.table_id,
                    page=eff_page,
                    top=grp,
                    mid="",
                    detail=piece,
                    section_path=sp,
                    tab=sheet,
                    source=format_financial_source(page=eff_page, table_id=r.table_id),
                )
            )
            ctx.doc_seq += 1


def _li_direct_ps(li: Tag) -> list[str]:
    texts: list[str] = []
    for ch in li.children:
        if isinstance(ch, NavigableString):
            continue
        if isinstance(ch, Tag) and ch.name == "p":
            t = norm(ch.get_text(" ", strip=True))
            if t:
                texts.append(t)
    return texts


def _process_li(li: Tag, ctx: _Ctx, doc_name: str, out: list[Req]) -> None:
    if not _collecting(ctx):
        return
    paras = _li_direct_ps(li)
    nested_ul = None
    for ch in li.children:
        if isinstance(ch, Tag) and ch.name == "ul":
            nested_ul = ch
            break

    if len(paras) == 1 and is_subgroup_label(paras[0]):
        ctx.list_group = preserve_group_label(paras[0])
        if nested_ul is not None:
            for sub in nested_ul.find_all("li", recursive=False):
                _process_li(sub, ctx, doc_name, out)
            return
        return

    i = 0
    while i < len(paras):
        p = paras[i]
        if is_subgroup_label(p):
            ctx.list_group = preserve_group_label(p)
            i += 1
            continue
        if is_numbered_detail_item(p):
            _append_req(out, doc_name=doc_name, ctx=ctx, detail=p)
            i += 1
            continue
        if _BULLET_START.match(p) or re.match(r"^[①②③④⑤⑥⑦⑧⑨⑩]", p):
            if (
                re.match(r"^[①②③④⑤⑥⑦⑧⑨⑩]", p)
                and i + 1 < len(paras)
                and _BULLET_START.match(paras[i + 1])
            ):
                _append_req(out, doc_name=doc_name, ctx=ctx, detail=f"{p}\n{paras[i + 1]}")
                i += 2
            else:
                _append_req(out, doc_name=doc_name, ctx=ctx, detail=p)
            i += 1
            continue
        title, body = split_title_body(p)
        if not title and ctx.list_splitter:
            title, body = ctx.list_splitter.split(p)
        if title and body and is_valid_req_group_label(title):
            ctx.list_group = title
            _append_req(
                out,
                doc_name=doc_name,
                ctx=ctx,
                detail=body,
                group=title,
                relax_gate=True,
            )
            i += 1
            continue
        if is_numbered_section_title(p):
            _apply_numbered_section(ctx, p, preview=_inline_preview(li))
            i += 1
            continue
        if len(p) > 12 and text_reads_as_requirement(p):
            _append_req(out, doc_name=doc_name, ctx=ctx, detail=p)
        i += 1

    if nested_ul is not None:
        for sub in nested_ul.find_all("li", recursive=False):
            _process_li(sub, ctx, doc_name, out)


def _process_ul(el: Tag, ctx: _Ctx, doc_name: str, out: list[Req]) -> None:
    if not _collecting(ctx) or el.find_parent("ul"):
        return
    for li in el.find_all("li", recursive=False):
        _process_li(li, ctx, doc_name, out)


def _process_p(el: Tag, ctx: _Ctx, doc_name: str, out: list[Req]) -> None:
    if el.find_parent("table") or el.find_parent("li"):
        return
    text = norm(el.get_text(" ", strip=True))
    if not text:
        return
    if _apply_numbered_section(ctx, text, preview=_inline_preview(el)):
        return
    if not _collecting(ctx):
        return
    if _BULLET_START.match(text):
        _append_req(out, doc_name=doc_name, ctx=ctx, detail=text)


def _json_heading_pages(doc: dict) -> dict[str, int]:
    pages: dict[str, int] = {}

    def walk(nodes) -> None:
        for el in nodes or []:
            if not isinstance(el, dict):
                continue
            if el.get("type") == "heading":
                c = norm(el.get("content", ""))
                p = el.get("page number")
                if c and p is not None:
                    pages[c] = int(p)
            walk(el.get("kids", []))

    walk(doc.get("kids", []))
    return pages


def _page_for_heading(text: str, heading_pages: dict[str, int]) -> int | None:
    t = norm(text)
    if not t or not heading_pages:
        return None
    if t in heading_pages:
        return heading_pages[t]
    best_p, best_len = None, 0
    for k, p in heading_pages.items():
        if t.startswith(k) or k.startswith(t):
            ln = min(len(t), len(k))
            if ln > best_len:
                best_len, best_p = ln, p
    return best_p


def _json_table_catalog(doc: dict) -> list[tuple[int | None, str]]:
    catalog: list[tuple[int | None, str]] = []

    def walk(nodes) -> None:
        for el in nodes or []:
            if not isinstance(el, dict):
                continue
            if el.get("type") == "table":
                blob: list[str] = []
                for row in el.get("rows", [])[:4]:
                    for cell in row.get("cells", []):
                        for kid in cell.get("kids", []):
                            if isinstance(kid, dict) and kid.get("type") == "paragraph":
                                blob.append(kid.get("content", ""))
                catalog.append((el.get("page number"), norm(" ".join(blob))[:160]))
            walk(el.get("kids", []))

    walk(doc.get("kids", []))
    return catalog


def _page_for_html_table(
    el: Tag,
    catalog: list[tuple[int | None, str]],
    used: set[int],
) -> int | None:
    blob = norm(el.get_text(" ", strip=True))[:160]
    if not blob:
        return None
    words = set(blob.split())
    best_i, best_score = -1, 0
    for i, (page, jsig) in enumerate(catalog):
        if i in used or not jsig:
            continue
        score = len(words & set(jsig.split()))
        if score > best_score:
            best_i, best_score = i, score
    if best_i >= 0 and best_score >= 4:
        used.add(best_i)
        return catalog[best_i][0]
    return None


def _collect_llm_prefetch(body: Tag) -> tuple[list[ZoneTask], list[str]]:
    """DOM walk 전 zone·리스트 분리 LLM 일괄 호출용 작업 수집."""
    headings: dict[int, str] = {}
    zone_tasks: list[ZoneTask] = []
    list_texts: set[str] = set()
    zone_seen: set[str] = set()

    def section_path() -> str:
        return " > ".join(headings[d] for d in sorted(headings))

    def queue_zone(heading: str, level: int, preview: str = "") -> None:
        sp = section_path()
        key = f"{sp}|{norm(heading)}"
        if key in zone_seen:
            return
        zone_seen.add(key)
        zone_tasks.append(ZoneTask(heading, level, sp, preview))

    for el in body.find_all(["h3", "h4", "h5", "h6", "table", "ul", "p", "li"]):
        if not isinstance(el, Tag) or el.find_parent("table"):
            continue
        tag = el.name
        if tag in ("h3", "h4", "h5", "h6"):
            t = norm(el.get_text(" ", strip=True))
            if not t or is_toc_line(t):
                continue
            level = int(tag[1])
            for d in list(headings):
                if d >= level:
                    del headings[d]
            headings[level] = t
            queue_zone(t, level, _heading_preview(el))
        elif tag == "p" and not el.find_parent("li"):
            t = norm(el.get_text(" ", strip=True))
            if t and is_numbered_section_title(t):
                parsed = parse_numbered_section(t)
                if parsed:
                    num, body_txt = parsed
                    full = preserve_group_label(f"{num}. {body_txt}")
                    level = heading_level_from_number(num)
                    for d in list(headings):
                        if d >= level:
                            del headings[d]
                    headings[level] = full
                    queue_zone(full, level, _inline_preview(el))
            elif len(t) > 12:
                list_texts.add(t)
        elif tag == "li":
            for p in el.find_all("p", recursive=False):
                t = norm(p.get_text(" ", strip=True))
                if not t:
                    continue
                if is_numbered_section_title(t):
                    parsed = parse_numbered_section(t)
                    if parsed:
                        num, body_txt = parsed
                        full = preserve_group_label(f"{num}. {body_txt}")
                        level = heading_level_from_number(num)
                        for d in list(headings):
                            if d >= level:
                                del headings[d]
                        headings[level] = full
                        queue_zone(full, level, _inline_preview(el))
                elif len(t) > 12 and not _BULLET_START.match(t):
                    list_texts.add(t)
    return zone_tasks, sorted(list_texts)


def extract_financial_from_html(
    html: str,
    doc_name: str,
    *,
    table_catalog: list[tuple[int | None, str]] | None = None,
    heading_pages: dict[str, int] | None = None,
    use_llm: bool = True,
    llm_concurrency: int | None = None,
) -> list[Req]:
    soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    ctx = _Ctx(
        splitter=make_splitter(use_llm=use_llm),
        zones=make_zone_classifier(use_llm=use_llm, concurrency=llm_concurrency),
        list_splitter=make_list_heading_splitter(use_llm=use_llm, concurrency=llm_concurrency),
        heading_pages=heading_pages or {},
    )
    if use_llm:
        zone_tasks, list_texts = _collect_llm_prefetch(body)
        ctx.zones.prefetch(zone_tasks)  # type: ignore[union-attr]
        ctx.list_splitter.prefetch(list_texts)  # type: ignore[union-attr]
    out: list[Req] = []
    used_tables: set[int] = set()

    for el in body.find_all(["h3", "h4", "h5", "h6", "table", "ul", "p"]):
        if not isinstance(el, Tag) or el.find_parent("table"):
            continue
        tag = el.name
        if tag in ("h3", "h4", "h5", "h6"):
            _update_heading(
                ctx,
                int(tag[1]),
                el.get_text(" ", strip=True),
                preview=_heading_preview(el),
            )
        elif tag == "table" and el.find_parent("table") is None:
            page = (
                _page_for_html_table(el, table_catalog, used_tables)
                if table_catalog
                else None
            )
            _process_table(el, ctx, doc_name, out, page=page)
        elif tag == "ul":
            _process_ul(el, ctx, doc_name, out)
        elif tag == "p":
            _process_p(el, ctx, doc_name, out)

    return _filter_appendix_reqs(out)


def _tab_order_from_reqs(reqs: list[Req]) -> list[str]:
    """시트 순서 — 문서 등장(DOM) 순."""
    order: list[str] = []
    seen: set[str] = set()
    for r in reqs:
        t = r.tab or "요구사항"
        if t not in seen:
            seen.add(t)
            order.append(t)
    return order


def extract_financial_rfp(
    *,
    doc_name: str,
    html: str,
    json_path: Path | None = None,
    use_llm: bool = True,
    llm_concurrency: int | None = None,
) -> tuple[list[Req], dict | None, list[str], list[str]]:
    steps: list[str] = []
    table_catalog: list[tuple[int | None, str]] | None = None
    heading_pages: dict[str, int] | None = None
    doc: dict | None = None
    if json_path and json_path.is_file():
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        table_catalog = _json_table_catalog(doc)
        heading_pages = _json_heading_pages(doc)
    reqs = extract_financial_from_html(
        html,
        doc_name,
        table_catalog=table_catalog,
        heading_pages=heading_pages,
        use_llm=use_llm,
        llm_concurrency=llm_concurrency,
    )
    mode = "LLM" if use_llm else "룰"
    steps.append(f"extract(financial/{mode} zone+cell): {len(reqs)} rows")

    overview: dict | None = None
    if doc is not None:
        overview = build_overview_sync(doc, reqs)
    else:
        overview = build_overview_from_html_sync(html, reqs)
    if overview:
        steps.append(
            f"overview: 요약 + 기술 {len(overview.get('techs', []))} + "
            f"리스크 {len(overview.get('risks', []))}"
        )

    sanitize_hierarchy_labels(reqs)
    tabs = sorted({r.tab for r in reqs}, key=lambda t: _tab_order_from_reqs(reqs).index(t))
    steps.append(f"tab: {len(tabs)}개 시트 — {', '.join(tabs[:6])}{'…' if len(tabs) > 6 else ''}")

    tab_order = _tab_order_from_reqs(reqs)
    return reqs, overview, steps, tab_order
