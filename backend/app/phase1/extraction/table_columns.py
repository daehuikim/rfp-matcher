from __future__ import annotations

import re

from bs4 import Tag

# 조견표 헤더·본문 판별용
REQ_CATEGORY_LABELS = ("요건 구분", "요건구분", "구분")
REQ_DETAIL_LABELS = ("상세내용", "상세 내용", "내용", "요구사항")

# 조견표 연속 표가 끊기는 지점 (제출서류·입찰서식 등)
STOP_TABLE_KEYWORDS = (
    "제출 서류",
    "회사명",
    "입찰보증",
    "생년월일",
    "제안 참불",
    "비밀유지확약서",
)

_REQ_BODY_RE = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩•·]|해야 합니다|제공해야|제안해야")


def header_looks_like_requirements(header: list[str]) -> bool:
    joined = " ".join(header)
    has_category = any(lbl in joined for lbl in REQ_CATEGORY_LABELS)
    has_detail = any(lbl in joined for lbl in REQ_DETAIL_LABELS)
    return has_category and has_detail


def detect_column_indices(header: list[str]) -> tuple[int, int]:
    """헤더 셀에서 '요건 구분'·'상세내용' 열 인덱스 추정."""
    category_col: int | None = None
    detail_col: int | None = None
    for i, h in enumerate(header):
        h = h.strip()
        if not h or len(h) > 40:
            continue
        if category_col is None and any(lbl in h for lbl in REQ_CATEGORY_LABELS):
            category_col = i
        if detail_col is None and any(lbl in h for lbl in REQ_DETAIL_LABELS):
            detail_col = i
    if category_col is None:
        category_col = 0
    if detail_col is None:
        detail_col = max(category_col + 1, len(header) - 1)
    return category_col, detail_col


def first_row_is_content(header_cells: list[str]) -> bool:
    """
    PyMuPDF 등이 페이지 경계에서 조견표를 쪼갤 때 첫 행이 헤더가 아니라
    본문(①·•·'해야 합니다' 등)으로 채워지는 경우.
    """
    for text in header_cells:
        if _REQ_BODY_RE.search(text) and len(text) > 40:
            return True
    return False


def is_short_header_row(header_cells: list[str]) -> bool:
    """'요건 구분 | 상세내용'처럼 짧은 라벨만 있는 진짜 헤더 행."""
    non_empty = [h for h in header_cells if h.strip()]
    if not non_empty:
        return False
    return all(len(h) < 50 for h in non_empty) and header_looks_like_requirements(header_cells)


def row_has_requirement_body(cells: list[Tag]) -> bool:
    for cell in cells:
        text = cell.get_text("\n", strip=True)
        if len(text) >= 30 and _REQ_BODY_RE.search(text):
            return True
    return False


def extract_category_and_detail(
    cells: list[Tag],
    *,
    category_col: int,
    detail_col: int,
) -> tuple[str | None, str]:
    texts = [c.get_text("\n", strip=True) for c in cells]

    detail = texts[detail_col] if detail_col < len(texts) else ""
    category = texts[category_col] if category_col < len(texts) else None

    # 상세열이 비었으면 가장 긴 본문 셀 사용 (PyMuPDF 병합 셀 대응)
    if len(detail) < 40:
        best = max(
            ((len(t), t) for i, t in enumerate(texts) if t and i != category_col),
            default=(0, ""),
            key=lambda x: x[0],
        )
        if best[0] >= 40:
            detail = best[1]

    # 분류: 짧은 라벨 셀 (헤더·프로젝트명 반복 제외)
    if not category or category == detail or len(category) > 80:
        for t in texts:
            if not t or t == detail or len(t) > 80:
                continue
            if "비정형 데이터 플랫폼" in t and len(t) < 30:
                continue
            category = t
            break

    return category, detail


def table_looks_like_requirement_continuation(tbl: Tag) -> bool:
    """조견표 본문이 이어지는 표인지 (페이지 분할 조각)."""
    first = tbl.find("tr")
    if not isinstance(first, Tag):
        return False
    header = [c.get_text(strip=True) for c in first.find_all(["td", "th"])]
    joined = " ".join(header)
    if any(kw in joined for kw in STOP_TABLE_KEYWORDS):
        return False
    if header_looks_like_requirements(header):
        return True
    if first_row_is_content(header):
        return True
    return row_has_requirement_body(first.find_all(["td", "th"]))
