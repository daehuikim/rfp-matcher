"""
한글(HWP/HWPX) 공공 RFP — '요구사항 총괄표' 기반 추출.

요구사항 총괄표 → 탭(SFR/FUN/…) 순서·개요 시트
세로/와이드 form 블록 → 요구사항 행 (원문 ID·명칭 보존)
그 외 표 → 부록 탭
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import Req, atomize_detail, format_source
from .form_table import _ID_PREFIX, _label_role
from .grid import Grid, _heading_from_context, _html_table_to_grid, read_html_bytes
from .grid import _reflow_inline_pipe_table
from .table_render import _parse_pipe_table
from .text import norm, norm_lines, sig

_REQ_ID = re.compile(r"^([A-Za-z]{2,4})[-_ ](\d+)$", re.I)
_SUMMARY_ID = re.compile(r"^([A-Za-z]{2,4})[-_ ]?(?:0{2,3}|O{2,3})$", re.I)
_SUMMARY_HEADING = re.compile(r"요구\s*사항\s*총괄\s*표")
_LIST_HEADING = re.compile(r"요구\s*사항\s*목록")


@dataclass
class SummaryTable:
    headers: list[str]
    rows: list[list[str]]
    tab_order: list[str] = field(default_factory=list)


@dataclass
class KoreanFormResult:
    reqs: list[Req]
    summary: SummaryTable | None
    steps: list[str]


def is_requirement_id(text: str) -> bool:
    t = norm(text)
    m = _REQ_ID.match(t)
    if not m:
        return False
    return int(m.group(2)) >= 1


def _tab_from_id(rid: str) -> str:
    m = _ID_PREFIX.match(rid.strip())
    return m.group(1).upper() if m else "요구사항"


def _tab_from_summary_id(text: str) -> str | None:
    m = _SUMMARY_ID.match(norm(text))
    return m.group(1).upper() if m else None


def _content_cells(row: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for i, cell in enumerate(row):
        # 불릿·표안표 줄바꿈 보존 (norm 은 공백만 collapse)
        c = norm_lines(cell) if ("\n" in cell or "|" in cell) else norm(cell)
        if not c or len(sig(c)) < 2:
            continue
        if is_requirement_id(c) or _tab_from_summary_id(c):
            continue
        # 앞쪽 열의 짧은 역할 라벨만 제외(긴 본문에 '정의' 등이 포함돼도 값으로 인정)
        if i <= 1 and _label_role(c) and len(c) <= 16:
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _first_content(row: list[str]) -> str:
    cells = _content_cells(row)
    if not cells:
        return ""
    return max(cells, key=len)


def _footer_value(row: list[str]) -> str:
    """산출정보·관련요구사항 값 — DAR-006 등 ID·와이드 form 본문도 허용."""
    label0 = _row_label(row)
    candidates: list[str] = []
    for i, cell in enumerate(row):
        if i == 0:
            continue
        c = norm(cell)
        if not c or c in _FOOTER_LABELS or c == label0:
            continue
        if i == 1 and _label_role(c) and c == label0:
            continue
        candidates.append(c)
    if not candidates:
        return ""
    return max(candidates, key=len)


_SKIP_RID_LABELS = frozenset({"산출정보", "관련요구사항", "산출물", "산출 정보"})
_FOOTER_LABELS = _SKIP_RID_LABELS


def _row_label(row: list[str]) -> str:
    return norm(row[0]) if row else ""


def _split_definition_value(val: str) -> tuple[str, list[str]]:
    """정의 셀에 ◦ 불릿이 섞인 경우 정의·불릿 분리 (DAR-001 등)."""
    val = val.strip()
    if "◦" not in val:
        return val, []
    head, tail = re.split(r"\s*◦\s*", val, maxsplit=1)
    bullets: list[str] = []
    if tail.strip():
        bullets.append(f"◦ {tail.strip()}")
    return head.strip(), bullets


def _append_detail_part(detail_parts: list[str], val: str) -> None:
    """세부내용 누적 — '하여야 함' 등 이전 불릿 꼬리 조각 병합."""
    val = val.strip()
    if not val:
        return
    val = re.sub(r"(?<=[^\n])\s*◦\s*", "\n◦ ", val)
    for line in (ln.strip() for ln in val.split("\n")):
        if not line:
            continue
        if line.startswith("◦"):
            norm_line = line if line.startswith("◦ ") else f"◦ {line[1:].strip()}"
            detail_parts.append(norm_line)
        elif detail_parts and detail_parts[-1].startswith("◦"):
            if "|" in line or "---" in line:
                detail_parts.append(line)
            else:
                detail_parts[-1] = f"{detail_parts[-1]} {line}".strip()
        else:
            detail_parts.append(f"◦ {line}" if not ("|" in line or "---" in line) else line)


_TABLE_CAPTION = re.compile(r"^<.+>$")
_TABLE_COL_HEADERS = frozenset(
    {"분류", "구분", "번호", "순번", "항목", "카테고리", "단계", "내용"}
)


def _norm_header_cell(c: str) -> str:
    """HWPX 열 헤더 — '분 류', '단 계' 등 내부 공백 제거."""
    return re.sub(r"\s+", "", norm(c))


def _is_table_caption_row(row: list[str]) -> bool:
    """<주요 점검항목 예시> 등 표 제목 단독 행."""
    label = _row_label(row)
    return bool(_TABLE_CAPTION.match(label)) and len([c for c in row if norm(c)]) <= 1


def _is_table_header_row(row: list[str]) -> bool:
    """form 라벨·footer 가 아닌 짧은 열 헤더 행."""
    label = _row_label(row)
    if label in _FORM_LABELS or label in _FOOTER_LABELS:
        return False
    if re.fullmatch(r"\d+", label):
        return False
    cells = [norm(c) for c in row if norm(c)]
    if len(cells) < 2:
        return False
    if not all(
        len(c) <= 24 and "◦" not in c and "하여야" not in c and " - " not in c[:6]
        for c in cells
    ):
        return False
    cells_n = [_norm_header_cell(c) for c in cells]
    label_n = _norm_header_cell(label)
    if cells_n[0] in _TABLE_COL_HEADERS and all(len(c) <= 12 for c in cells_n):
        return True
    if len(cells_n) == 2 and cells_n[0] in ("분류", "구분", "단계"):
        return True
    role = _label_role(label)
    if role and role not in ("id",):
        return False
    if label_n in _TABLE_COL_HEADERS:
        return True
    return False


def _collect_embedded_table_rows(
    rows: list[list[str]], start: int
) -> tuple[list[list[str]], int]:
    """세부내용 직후 삽입된 데이터 표 행(순번|분류|구성방안 등)."""
    if start >= len(rows) or not _is_table_header_row(rows[start]):
        return [], 0
    width = len([c for c in rows[start] if norm(c)])
    table = [[norm(c) for c in rows[start] if norm(c)]]
    i = start + 1
    while i < len(rows):
        label = _row_label(rows[i])
        if label in _FOOTER_LABELS or label in _FORM_LABELS:
            break
        role = _label_role(label)
        if role and role not in ("id",):
            break
        if role == "id" and not re.fullmatch(r"\d+", label):
            break
        cells = [norm(c) for c in rows[i] if norm(c)]
        if len(cells) >= 2 and len(cells) <= width + 1:
            table.append(cells[:width])
            i += 1
            continue
        break
    return (table, i - start) if len(table) >= 2 else ([], 0)


def _rows_to_pipe_markdown(table_rows: list[list[str]]) -> str:
    if not table_rows:
        return ""
    headers = [norm(c) for c in table_rows[0] if norm(c)]
    if not headers:
        return ""
    lines = [" | ".join(headers), " | ".join(["---"] * len(headers))]
    for row in table_rows[1:]:
        cells = [norm(c) for c in row][: len(headers)]
        cells.extend([""] * (len(headers) - len(cells)))
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _flat_table_chunks(table_rows: list[list[str]]) -> list[str]:
    headers = [norm(c) for c in table_rows[0] if norm(c)]
    norm_h = [_norm_header_cell(c) for c in headers]
    flat = "".join(headers)
    norm_flat = "".join(norm_h)
    for row in table_rows[1:]:
        body = "".join(norm(c) for c in row if norm(c))
        flat += body
        norm_flat += body
    out: list[str] = []
    for c in (flat, norm_flat, "".join(headers), "".join(norm_h)):
        if c and c not in out:
            out.append(c)
    return out


def _strip_flat_table_suffix(detail: str, table_rows: list[list[str]]) -> str:
    """HWPX 평탄화 표 꼬리(순번분류구성방안1검색…) 제거."""
    if not detail or not table_rows:
        return detail
    for chunk in _flat_table_chunks(table_rows):
        if chunk in detail:
            detail = detail.replace(chunk, "", 1).strip()
    return detail.rstrip("- ").strip()


def _inline_flat_span(val: str, table_rows: list[list[str]]) -> tuple[int, int] | None:
    """HWPX 평탄화 표 꼬리 구간 — 완전·부분 헤더 매칭(<캡션>번호점검… 등)."""
    headers = [norm(c) for c in table_rows[0] if norm(c)]
    if not headers:
        return None
    norm_h = [_norm_header_cell(c) for c in headers]
    candidates: list[str] = []
    for hdrs in (headers, norm_h):
        candidates.append("".join(hdrs))
        if len(hdrs) >= 2:
            candidates.append(hdrs[0] + hdrs[1])
        if len(hdrs) >= 3:
            candidates.append("".join(hdrs[:3]))
        candidates.append(hdrs[0])
    candidates = [c for c in dict.fromkeys(candidates) if len(c) >= 4]
    for pat in candidates:
        if len(pat) < 4:
            continue
        m = re.search(rf"(?:<[^>]+>)?{re.escape(pat)}", val)
        if not m:
            continue
        start = m.start()
        if val[start : start + 1] == "<":
            start = val.find(pat, start)
        tail = val[start:]
        end_m = re.search(r"(?:※|(?<=[^\n])\s*◦\s*)", tail)
        end = start + (end_m.start() if end_m else len(tail))
        return start, end
    return None


def _embed_pipe_table_in_detail(val: str, table_rows: list[list[str]], md: str) -> str:
    """평탄화 표 꼬리를 pipe markdown 으로 치환 — SFR-002(※ 앞), PSR-014(<캡션> 뒤)."""
    val = val.strip()
    if not md:
        return val
    chunks = _flat_table_chunks(table_rows)
    if chunks and chunks[0] in val:
        return val.replace(chunks[0], f"\n{md}\n", 1).strip()
    span = _inline_flat_span(val, table_rows)
    if span:
        start, end = span
        return f"{val[:start].strip()}\n{md}\n{val[end:].strip()}".strip()
    m = re.search(r"※", val)
    if m:
        return f"{val[: m.start()].strip()}\n{md}\n{val[m.start() :].strip()}".strip()
    return f"{val}\n{md}".strip() if val else md


def _detail_needs_orphan_table(detail: str, table_rows: list[list[str]]) -> bool:
    """세부내용에 평탄화 표 꼬리가 남아 있고 pipe 표가 아직 없음."""
    if not detail or not table_rows:
        return False
    if _parse_pipe_table(_reflow_inline_pipe_table(detail)):
        return False
    if _inline_flat_span(detail, table_rows):
        return True
    hdr = "".join(_norm_header_cell(c) for c in table_rows[0] if norm(c))
    return bool(hdr) and len(hdr) >= 4 and hdr in sig(detail)


def _is_orphan_data_table(grid: Grid) -> bool:
    """HWPX가 form 밖으로 분리한 데이터 표 — 고유번호·세부내용 라벨 없음."""
    if not grid.cells or len(grid.cells) < 2:
        return False
    if _grid_has_form_rid(grid):
        return False
    blob = " ".join(" ".join(r) for r in grid.cells)
    if "요구사항" in blob or "세부내용" in blob or "세부 내용" in blob:
        return False
    if "◦" in blob:
        return False
    return _is_table_header_row(grid.cells[0])


def _match_orphan_table_to_req(
    table_rows: list[list[str]], reqs: list[Req]
) -> Req | None:
    """평탄화 인라인 꼬리가 있는 요구사항에 orphan 표 재결합."""
    hits = [r for r in reqs if _detail_needs_orphan_table(r.detail, table_rows)]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    return max(hits, key=lambda r: len(sig(r.detail)))


def _attach_orphan_table(req: Req, grid: Grid) -> None:
    md = _rows_to_pipe_markdown(grid.cells)
    req.detail = _embed_pipe_table_in_detail(req.detail, grid.cells, md)
    tag = f"표#{grid.table_id}"
    if tag not in (req.source or ""):
        req.source = f"{req.source or ''} + {tag}".strip()


def _detail_value_col(row: list[str]) -> int:
    best_i, best_len = 0, -1
    for i, cell in enumerate(row):
        if i == 0 and _row_label(row) in _FORM_LABELS:
            continue
        c = norm(cell)
        if c and len(c) > best_len:
            best_len = len(c)
            best_i = i
    return best_i


def _is_detail_row(row: list[str]) -> bool:
    label0 = _row_label(row)
    sub = norm(row[1]) if len(row) > 1 else ""
    if label0 in ("세부내용", "세부 내용") or "세부" in label0:
        return True
    if label0 == "요구사항 상세설명" and "정의" not in sub:
        return _label_role(sub) == "detail" or "세부" in sub
    return _label_role(label0) == "detail" and "정의" not in sub


def _preprocess_form_block_rows(rows: list[list[str]]) -> list[list[str]]:
    """form 블록 내 표 행 → pipe markdown 병합 (DAR-009 등)."""
    out: list[list[str]] = []
    i = 0
    while i < len(rows):
        row = rows[i]
        if _is_detail_row(row):
            start = i + 1
            while start < len(rows) and _is_table_caption_row(rows[start]):
                start += 1
            table, consumed = _collect_embedded_table_rows(rows, start)
            if table:
                consumed += start - (i + 1)
            if table:
                md = _rows_to_pipe_markdown(table)
                col = _detail_value_col(row)
                row = list(row)
                while len(row) <= col:
                    row.append("")
                row[col] = _embed_pipe_table_in_detail(row[col], table, md)
                out.append(row)
                i += 1 + consumed
                continue
        out.append(row)
        i += 1
    return out


def _extract_form_rid(rows: list[list[str]]) -> str:
    """고유번호 행 우선 — 산출정보(DAR-008 등) 오인식 방지, 와이드 form 헤더 ID 허용."""
    for row in rows:
        role = _label_role(_row_label(row))
        if role != "id":
            continue
        val = _first_content(row)
        if val and is_requirement_id(val):
            return norm(val)
        for cell in row:
            if is_requirement_id(cell):
                return norm(cell)
    for row in rows:
        lbl = _row_label(row)
        if lbl in _SKIP_RID_LABELS or "산출" in lbl:
            continue
        for cell in row:
            if is_requirement_id(cell):
                return norm(cell)
    return ""


def pivot_form_rows(
    rows: list[list[str]],
    *,
    doc_name: str,
    table_id: int,
    page: int | None = None,
    allow_no_id: bool = False,
    require_detail: bool = True,
) -> Req | None:
    """단일 form 블록(세로·와이드) → Req 1건."""
    if not rows:
        return None
    rows = _preprocess_form_block_rows(rows)
    rid = ""
    category = item = defn = ""
    detail_parts: list[str] = []
    deliverable = related_req = ""

    for row in rows:
        label0 = _row_label(row)
        role = _label_role(label0)
        sub = norm(row[1]) if len(row) > 1 else ""
        if role is None and sub:
            role = _label_role(sub)

        if label0 in ("산출정보", "산출물", "산출 정보"):
            deliverable = _footer_value(row) or deliverable
        elif label0 == "관련요구사항":
            related_req = _footer_value(row) or related_req
        elif role == "category":
            category = _first_content(row)
        elif role == "item":
            item = _first_content(row)
        elif role == "detail":
            val = _first_content(row)
            if "정의" in sub or sub == "정의":
                d, bullets = _split_definition_value(val)
                defn = d or defn
                for b in bullets:
                    _append_detail_part(detail_parts, b)
            elif val:
                _append_detail_part(detail_parts, val)
        elif label0 in ("세부내용", "세부 내용") or "세부" in label0:
            val = _first_content(row)
            if val:
                _append_detail_part(detail_parts, val)

    rid = _extract_form_rid(rows)

    if not rid and not allow_no_id:
        return None
    detail = "\n".join(detail_parts).strip() if detail_parts else defn
    if require_detail and len(sig(detail)) < 2:
        return None
    tab = _tab_from_id(rid) if rid else ""
    src = format_source(table_id=table_id, page=page, section="") + (f" · {rid}" if rid else "")
    return Req(
        doc=doc_name,
        table_id=table_id,
        page=page,
        rid=rid,
        top=norm(item or category),
        mid=norm(defn),
        detail=detail,
        deliverable=norm(deliverable),
        related_req=norm(related_req),
        source=src,
        tab=tab or "요구사항",
    )


_FORM_LABELS = frozenset(
    {"요구사항 분류", "요구사항 고유번호", "요구사항 명칭", "요구사항 상세설명", "산출정보", "산출물"}
)

# PDF 변환 시 고유번호 셀이 비는 섹션 — h6 제목 → 탭 접두사
_SECTION_TAB_HINTS: tuple[tuple[str, str], ...] = (
    ("성능 요구", "PER"),
    ("인터페이스 요구", "SIR"),
    ("제약사항", "COR"),
    ("테스트 요구", "TER"),
    ("데이터 요구", "DAR"),
    ("보안 요구", "SER"),
    ("품질 요구", "QUR"),
    ("프로젝트 관리 요구", "PMR"),
    ("프로젝트 지원", "PSR"),
    ("기능 요구", "SFR"),
)


def _tab_from_section_heading(heading: str) -> str | None:
    h = heading or ""
    for kw, tab in _SECTION_TAB_HINTS:
        if kw in h:
            return tab
    return None


def _assign_section_id(req: Req, section: str, seq: dict[str, int]) -> None:
    if req.rid or not section:
        return
    seq[section] = seq.get(section, 0) + 1
    req.rid = f"{section}-{seq[section]:03d}"
    req.tab = section
    req.gen_rid = True


def _is_unlabeled_form_block(grid: Grid) -> bool:
    """고유번호 없이 form 행만 있는 블록(PDF에서 ID 셀 공란)."""
    if any(is_requirement_id(c) for row in grid.cells for c in row):
        return False
    blob = " ".join(" ".join(r) for r in grid.cells)
    if "요구사항 명칭" not in blob:
        return False
    roles = sum(1 for row in grid.cells for c in row if _label_role(c))
    # 헤더-only 3행(분류·고유번호·명칭) — PER-010·PSR-014 등 본문 표 분리
    return roles >= 3 and grid.nrows >= 3


def _merge_pending_detail(pending: Req, grid: Grid, *, doc_name: str) -> None:
    """분할 form(헤더 표 + 본문 표) — PSR-014 등."""
    body = pivot_form_rows(
        grid.cells,
        doc_name=doc_name,
        table_id=grid.table_id,
        page=grid.page,
        allow_no_id=True,
        require_detail=False,
    )
    if body:
        pending.mid = pending.mid or body.mid
        if body.detail:
            pending.detail = body.detail
        if body.deliverable:
            pending.deliverable = body.deliverable
        if body.related_req:
            pending.related_req = body.related_req
    d, rel = _extract_form_footer(grid.cells)
    if d:
        pending.deliverable = d
    if rel:
        pending.related_req = rel
    if len(sig(pending.detail)) < 2:
        pending.detail = _continuation_text(grid)
    if f"표#{grid.table_id}" not in (pending.source or ""):
        pending.source = f"{pending.source} + 표#{grid.table_id}"


def _grid_has_form_rid(grid: Grid) -> bool:
    """본문 고유번호 — 산출정보·관련요구사항 행의 DAR-006 등은 제외."""
    return any(_row_has_content_rid(row) for row in grid.cells)


def _extract_form_footer(rows: list[list[str]]) -> tuple[str, str]:
    deliverable = related = ""
    for row in rows:
        label = _row_label(row)
        if label in ("산출정보", "산출물", "산출 정보"):
            deliverable = _footer_value(row) or deliverable
        elif label == "관련요구사항":
            related = _footer_value(row) or related
    return deliverable, related


_PROPOSAL_APPENDIX_MARKERS = (
    "제안개요",
    "제안업체",
    "목 차",
    "작 성 방 법",
    "작성방법",
    "평가 부문",
    "입찰참가",
    "사업수행계획서",
    "제안서",
    "제안 안내",
    "회 사 명",
    "입찰참가신청서",
)


def _is_proposal_appendix_grid(grid: Grid) -> bool:
    """요구사항 본문 끝 — 제안서·평가표 등(continuation 오인 방지)."""
    if not grid.cells or _grid_has_form_rid(grid):
        return False
    blob = " ".join(" ".join(r) for r in grid.cells)
    return any(m in blob for m in _PROPOSAL_APPENDIX_MARKERS)


def _is_form_continuation(grid: Grid) -> bool:
    """ID 없이 직전 form 블록 세부내용·중첩표가 이어지는 표."""
    if not grid.cells:
        return False
    blob = " ".join(" ".join(r) for r in grid.cells)
    if _is_orphan_data_table(grid):
        return False
    if _is_proposal_appendix_grid(grid):
        return False
    if _grid_has_form_rid(grid):
        return False
    if " | " in blob and "---" in blob:
        return True
    if "◦" in blob or "▪" in blob or "□" in blob:
        return True
    if any(lbl in blob for lbl in ("세부내용", "세부 내용", "figcaption")):
        return True
    # 빈 헤더 + 본문만 있는 잔여 form 조각
    if grid.nrows <= 3 and len(sig(blob)) > 40:
        first = " ".join(grid.cells[0]) if grid.cells else ""
        if not any(is_requirement_id(c) for c in grid.cells[0]):
            return True
    return False


def _continuation_text(grid: Grid) -> str:
    """연속 form 표에서 상세요건에 붙일 텍스트."""
    if grid.nrows >= 2 and _is_table_header_row(grid.cells[0]):
        return _rows_to_pipe_markdown(grid.cells)
    parts: list[str] = []
    seen: set[str] = set()
    for row in grid.cells:
        if _row_label(row) in _FOOTER_LABELS:
            continue
        for cell in row:
            c = norm_lines(cell)
            if not c or len(sig(c)) < 3:
                continue
            if c in _FORM_LABELS or c in seen:
                continue
            if _label_role(c) and len(c) <= 16:
                continue
            if is_requirement_id(c):
                continue
            seen.add(c)
            parts.append(c)
    return "\n".join(parts).strip()


_TABLE_BLOCK = re.compile(
    r"(?:^|\n)([^\n]+\|[^\n]*\n[^\n]*---[^\n]*\n(?:[^\n]+\|[^\n]*\n?)+)",
    re.MULTILINE,
)


def split_public_detail(detail: str) -> list[str]:
    """공공 form 상세요건 — ◦ 불릿 분할, 표안표(마크다운) 블록은 한 덩어리 유지."""
    text = detail.strip()
    if not text:
        return []
    text = _reflow_inline_pipe_table(text)
    # 한 줄에 이어진 ◦ 불릿 → 줄 단위 분리
    text = re.sub(r"(?<=[^\n])\s*◦\s*", "\n◦ ", text)
    tables: list[str] = []

    def _stash(m: re.Match) -> str:
        tables.append(m.group(1).strip())
        return f"\n__TABLE_{len(tables) - 1}__\n"

    text = _TABLE_BLOCK.sub(_stash, text)
    chunks = re.split(r"(?:^|\n)\s*◦\s*", text)
    if len(chunks) <= 1 and "◦" in text:
        chunks = re.split(r"\s*◦\s*", text)
    out: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        stashed = "__TABLE_" in chunk
        for i, tbl in enumerate(tables):
            chunk = chunk.replace(f"__TABLE_{i}__", tbl)
        # _TABLE_BLOCK 으로 이미 분리된 pipe 표 — '순번 | 분류 |' 헤더 중간 재분할 방지
        if not stashed:
            # 불릿 본문 뒤에 붙은 표안표 분리 (행 시작 '분류|' 등만)
            tbl_m = re.search(
                r"(?:^|\n)\s*((?:구분|분류|항목)\s*\|[^\n]+\n[^\n]*---.+)",
                chunk,
                re.DOTALL,
            )
        else:
            tbl_m = None
        if tbl_m:
            prefix = chunk[: tbl_m.start()].strip()
            table_part = tbl_m.group(1).strip()
            if prefix:
                if not prefix.startswith("◦") and not prefix.startswith("※"):
                    prefix = f"◦ {prefix}"
                out.append(prefix)
            out.append(_reflow_inline_pipe_table(table_part))
            continue
        if not chunk.startswith("◦") and not chunk.startswith("※"):
            chunk = f"◦ {chunk}"
        out.append(chunk)
    return out or [detail]


def expand_detail_rows(reqs: list[Req]) -> list[Req]:
    """상세요건 ◦/□/① 등 불릿 단위로 행 분할 — ID·항목명·요구사항은 동일(셀 병합용)."""
    out: list[Req] = []
    for r in reqs:
        if r.tab == "부록":
            out.append(r)
            continue
        pieces = split_public_detail(r.detail)
        if len(pieces) == 1 and pieces[0] == r.detail:
            # pipe markdown 표 — 줄 단위 atomize 시 헤더·데이터 행이 분리됨 (DAR-009)
            if _parse_pipe_table(_reflow_inline_pipe_table(r.detail)) is None:
                pieces = atomize_detail(r.detail, "fine")
        if len(pieces) <= 1:
            out.append(r)
            continue
        for piece in pieces:
            out.append(
                Req(
                    doc=r.doc,
                    table_id=r.table_id,
                    page=r.page,
                    rid=r.rid,
                    top=r.top,
                    mid=r.mid,
                    detail=piece,
                    deliverable=r.deliverable,
                    related_req=r.related_req,
                    section_path=r.section_path,
                    tab=r.tab,
                    source=r.source,
                    gen_rid=r.gen_rid,
                    gen_top=r.gen_top,
                    gen_mid=r.gen_mid,
                    detail_images=list(r.detail_images),
                )
            )
    return out


def public_form_overview(summary: SummaryTable, meta: dict | None) -> dict:
    """총괄표 + 개요(요약·핵심기술·리스크) 복합 overview."""
    ov: dict = {
        "type": "public_rfp",
        "sheet_title": "요구사항 총괄표",
        "headers": summary.headers,
        "rows": summary.rows,
    }
    if meta:
        ov["meta"] = meta
    return ov


def _row_has_content_rid(row: list[str]) -> bool:
    if _row_label(row) in _FOOTER_LABELS:
        return False
    return any(is_requirement_id(c) for c in row)


def split_rows_by_requirement_ids(rows: list[list[str]]) -> list[list[list[str]]]:
    """병합된 mega-table(hwp5html/hwpx)을 ID 경계로 분할."""
    blocks: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for row in rows:
        has_id = _row_has_content_rid(row)
        if has_id and cur:
            blocks.append(cur)
            cur = [row]
        else:
            cur.append(row)
    if cur:
        blocks.append(cur)
    return blocks


def _is_summary_table(grid: Grid) -> bool:
    heading = grid.section_heading or ""
    if _SUMMARY_HEADING.search(heading):
        return True
    blob = " ".join(" ".join(r) for r in grid.cells[:4])
    if "요구사항번호" in blob or "요구사항 번호" in blob:
        if any(_tab_from_summary_id(c) for row in grid.cells for c in row):
            return True
    if "ID부여" in blob or "ID 부여" in blob:
        return True
    return False


def _is_requirement_list_table(grid: Grid) -> bool:
    heading = grid.section_heading or ""
    if _LIST_HEADING.search(heading):
        return True
    if not grid.cells:
        return False
    header = " ".join(grid.cells[0])
    return "요구사항 명칭" in header and "번호" in header and grid.nrows > 8


def parse_summary_table(grid: Grid) -> SummaryTable:
    headers = [norm(c) for c in grid.cells[0]] if grid.cells else []
    rows: list[list[str]] = []
    tab_order: list[str] = []
    for row in grid.cells[1:]:
        cells = [norm(c) for c in row]
        if not any(cells) or all("합" in c and "계" in c for c in cells if c):
            continue
        rid_cell = next((c for c in cells if _tab_from_summary_id(c)), "")
        tab = _tab_from_summary_id(rid_cell) if rid_cell else None
        if tab and tab not in tab_order:
            tab_order.append(tab)
        rows.append(cells)
    if not headers:
        headers = ["요청사항 구분", "ID부여규칙", "요구사항수"]
    return SummaryTable(headers=headers, rows=rows, tab_order=tab_order)


def find_summary_table(grids: list[Grid]) -> tuple[Grid, int] | None:
    for i, g in enumerate(grids):
        if _is_summary_table(g):
            return g, i
    return None


def grids_all_tables(html: str) -> list[Grid]:
    """merge 없이 top-level 표 전부 — form 블록 보존."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    top = [t for t in soup.find_all("table") if t.find_parent("table") is None]
    out: list[Grid] = []
    for i, t in enumerate(top):
        g = _html_table_to_grid(t, i)
        g.section_heading = _heading_from_context(soup, t)
        out.append(g)
    return out


def _appendix_from_grid(doc_name: str, grid: Grid) -> list[Req]:
    """비요구 표 — 표(table_id)당 동일 부록 ID, 행별 top/mid/detail 계위."""
    heading = norm(grid.section_heading or "") or f"표#{grid.table_id}"
    out: list[Req] = []
    for ri, row in enumerate(grid.cells):
        cells = [norm(c) for c in row if c]
        if not cells:
            continue
        detail = norm(" | ".join(cells))
        if len(sig(detail)) < 8:
            continue
        mid = cells[0][:60] if len(cells) > 1 and len(cells[0]) < 50 else ""
        body = norm(" | ".join(cells[1:])) if mid else detail
        if len(sig(body)) < 8:
            body = detail
            mid = ""
        out.append(
            Req(
                doc=doc_name,
                table_id=grid.table_id,
                page=grid.page,
                top=heading[:80],
                mid=mid,
                detail=body[:800],
                source=format_source(
                    table_id=grid.table_id, page=grid.page, section=grid.section_heading
                ),
                tab="부록",
            )
        )
    if not out:
        return []
    if len(out) == 1:
        out[0].mid = ""
    return out


def extract_korean_form_document(html: str, doc_name: str) -> KoreanFormResult | None:
    """총괄표가 있으면 한글 form 경로, 없으면 None(PDF 등 기존 경로)."""
    grids = grids_all_tables(html)
    found = find_summary_table(grids)
    if found is None:
        return None

    summary_grid, _ = found
    summary = parse_summary_table(summary_grid)
    steps: list[str] = [f"한글 form: 요구사항 총괄표 → 탭 {len(summary.tab_order)}개"]
    reqs: list[Req] = []
    appendix: list[Req] = []
    last_form_req: Req | None = None
    pending: Req | None = None
    section_seq: dict[str, int] = {}
    current_section = ""
    skip_non_req_tail = False

    for grid in grids:
        if skip_non_req_tail:
            continue
        if _is_summary_table(grid) or _is_requirement_list_table(grid):
            continue

        tab_hint = _tab_from_section_heading(grid.section_heading)
        if tab_hint:
            current_section = tab_hint

        blob = " ".join(" ".join(r) for r in grid.cells)
        form_rid = _extract_form_rid(grid.cells)
        has_req_id = bool(form_rid)

        if pending and not has_req_id:
            _merge_pending_detail(pending, grid, doc_name=doc_name)
            if len(sig(pending.detail)) >= 2:
                reqs.append(pending)
                last_form_req = pending
                pending = None
            continue

        if has_req_id:
            block = None
            if grid.nrows <= 12 and grid.nrows >= 2:
                block = pivot_form_rows(
                    grid.cells, doc_name=doc_name, table_id=grid.table_id, page=grid.page
                )
                if block is None:
                    block = pivot_form_rows(
                        grid.cells,
                        doc_name=doc_name,
                        table_id=grid.table_id,
                        page=grid.page,
                        require_detail=False,
                    )
            else:
                for block_rows in split_rows_by_requirement_ids(grid.cells):
                    if not any(is_requirement_id(c) for row in block_rows for c in row):
                        continue
                    b = pivot_form_rows(
                        block_rows, doc_name=doc_name, table_id=grid.table_id, page=grid.page
                    )
                    if b is None:
                        b = pivot_form_rows(
                            block_rows,
                            doc_name=doc_name,
                            table_id=grid.table_id,
                            page=grid.page,
                            require_detail=False,
                        )
                    if b:
                        if len(sig(b.detail)) < 2:
                            pending = b
                            last_form_req = None
                            continue
                        reqs.append(b)
                        last_form_req = b
                continue
            if block:
                if len(sig(block.detail)) < 2:
                    pending = block
                    last_form_req = None
                    continue
                reqs.append(block)
                last_form_req = block
            continue

        if _is_unlabeled_form_block(grid) and current_section:
            block = pivot_form_rows(
                grid.cells,
                doc_name=doc_name,
                table_id=grid.table_id,
                page=grid.page,
                allow_no_id=True,
            )
            if block is None:
                block = pivot_form_rows(
                    grid.cells,
                    doc_name=doc_name,
                    table_id=grid.table_id,
                    page=grid.page,
                    allow_no_id=True,
                    require_detail=False,
                )
            if block:
                _assign_section_id(block, current_section, section_seq)
                if len(sig(block.detail)) < 2:
                    pending = block
                    last_form_req = None
                    continue
                reqs.append(block)
                last_form_req = block
            continue

        if _is_orphan_data_table(grid):
            matched = _match_orphan_table_to_req(grid.cells, reqs)
            if matched:
                _attach_orphan_table(matched, grid)
            last_form_req = None
            continue

        if last_form_req and _is_form_continuation(grid):
            if _is_proposal_appendix_grid(grid):
                last_form_req = None
                skip_non_req_tail = True
                continue
            extra = _continuation_text(grid)
            d, rel = _extract_form_footer(grid.cells)
            if extra:
                last_form_req.detail = f"{last_form_req.detail}\n{extra}".strip()
                last_form_req.source = (
                    f"{last_form_req.source} + 표#{grid.table_id}"
                )
            if d:
                last_form_req.deliverable = d
            if rel:
                last_form_req.related_req = rel
            continue

        if grid.nrows >= 2 and len(sig(blob)) > 20:
            appendix.extend(_appendix_from_grid(doc_name, grid))
            last_form_req = None

    before_split = len(reqs)
    reqs = expand_detail_rows(reqs)
    if len(reqs) != before_split:
        steps.append(f"상세요건 불릿 분할: {before_split}→{len(reqs)}행")

    if appendix:
        steps.append(
            f"부록 제외: 비요구 표 {len({r.table_id for r in appendix})}개 · {len(appendix)}행 스킵"
        )

    tab_rank = {t: i for i, t in enumerate(summary.tab_order)}
    reqs.sort(
        key=lambda r: (
            tab_rank.get(r.tab, 999),
            r.rid or "",
            r.table_id,
        )
    )
    steps.append(f"form 추출: {len(reqs)}건 (탭 {len({r.tab for r in reqs})}개)")
    return KoreanFormResult(reqs=reqs, summary=summary, steps=steps)


def summary_as_overview(summary: SummaryTable) -> dict:
    """엑셀 첫 시트 — 요구사항 총괄표 그대로."""
    return {
        "type": "summary_table",
        "sheet_title": "요구사항 총괄표",
        "headers": summary.headers,
        "rows": summary.rows,
    }
