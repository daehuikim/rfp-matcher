from __future__ import annotations

import re

from bs4 import Tag

# 조견표 헤더·본문 판별용
REQ_CATEGORY_LABELS = ("요건 구분", "요건구분", "구분", "구 분")
REQ_DETAIL_LABELS = (
    "상세내용",
    "상세 내용",
    "내용",
    "요구사항",
    "요구사항 상세",
    "구축 범위",
    "요구 내용",
    "요구사항명",
    "요건명",
)

def _header_triggers_table_stop(header: list[str]) -> bool:
    """조견표 연속 판별 시 — 짧은 헤더(제출서류·일정 표)에서만 종료 키워드 적용."""
    non_empty = [h for h in header if h.strip()]
    if not non_empty:
        return False
    if any(len(h) >= 80 for h in non_empty):
        return False
    joined = " ".join(header)
    return any(kw in joined for kw in STOP_TABLE_KEYWORDS)


# 조견표 연속 표가 끊기는 지점 (제출서류·입찰서식·일정 등)
STOP_TABLE_KEYWORDS = (
    "제출 서류",
    "제출서류",
    "회사명",
    "입찰보증",
    "생년월일",
    "제안 참불",
    "비밀유지확약서",
    "일 정",
    "일정",
    "RFP 발송",
    "제안 마감",
    "표준 환경",
    "목 차",
    "목차",
)

_REQ_BODY_RE = re.compile(
    r"[①②③④⑤⑥⑦⑧⑨⑩•·□○●∙]|해야 합니다|제공해야|제안해야|구축해야|수행해야"
)
_MAX_CATEGORY_LEN = 48
_PROJECT_TITLE = "비정형 데이터 플랫폼 구축"


def is_valid_category_label(text: str | None) -> bool:
    """조견표 '요건 구분' 셀처럼 짧은 라벨인지 — 본문이 분류로 들어오는 것을 차단."""
    if not text or not text.strip():
        return False
    t = " ".join(text.split())
    if len(t) > _MAX_CATEGORY_LEN:
        return False
    if _REQ_BODY_RE.search(t):
        return False
    if t.count("•") >= 2:
        return False
    if t.strip() == _PROJECT_TITLE:
        return False
    if _PROJECT_TITLE in t and len(t) > len(_PROJECT_TITLE) + 4:
        return False
    return True


def normalize_category_label(text: str | None) -> str | None:
    if not text:
        return None
    t = " ".join(text.split())
    return t if is_valid_category_label(t) else None


def _normalize_header_cell(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def _cell_matches_label(cell: str, label: str) -> bool:
    """셀 전체가 라벨과 일치할 때만 True — 「운영구분」이 「구분」으로 오인되지 않게."""
    cell_norm = _normalize_header_cell(cell)
    label_norm = _normalize_header_cell(label)
    if not cell_norm or not label_norm:
        return False
    if cell_norm == label_norm:
        return True
    # 4자 이상 복합 라벨만 짧은 변형 허용 (「요구사항 상세」 등)
    if len(label_norm) >= 4 and label_norm in cell_norm and len(cell_norm) <= len(label_norm) + 2:
        return True
    return False


def _header_has_any_label(header: list[str], labels: tuple[str, ...]) -> bool:
    return any(_cell_matches_label(h, lbl) for h in header for lbl in labels)


def header_looks_like_requirements(header: list[str]) -> bool:
    has_category = _header_has_any_label(header, REQ_CATEGORY_LABELS)
    has_detail = _header_has_any_label(header, REQ_DETAIL_LABELS)
    return has_category and has_detail


# 조견표가 아닌 표 헤더 (S/W 역할·HW 스펙 등)
NON_REQUIREMENT_HEADER_COLUMNS = (
    "주요 역할",
    "제안 기준",
    "운영구분",
    "CPU",
    "GPU",
    "MEM",
    "Socket",
    "VRAM",
    "Core",
    "Type",
    "모델",
    "수량",
)

# 헤더에 요구·요건·요청·상세 등이 있어야 조견표 후보 (「제안 기준」만으로는 불충분)
REQ_TABLE_HEADER_KEYWORDS = (
    "요구",
    "요건",
    "요청",
    "요구사항",
    "상세내용",
    "상세 내용",
    "구축 범위",
    "요구 내용",
)


def header_has_requirement_keyword(header: list[str]) -> bool:
    """헤더 셀에 요구·요건·구분 등 조견표 힌트가 있으면 True (부분 문자열 오매칭 방지)."""
    tokens = (
        *REQ_CATEGORY_LABELS,
        *REQ_DETAIL_LABELS,
        "요건",
        "요구",
        "요청",
        "기능",
        "성능",
        "항목",
        "분류",
        "명칭",
    )
    return _header_has_any_label(header, tokens)


def header_is_requirement_table(header: list[str]) -> bool:
    """조견표로 추출할 표인지 — 헤더 기준 (요구/요청/상세 키워드 없으면 제외)."""
    if is_administrative_table_header(header):
        return False
    if header_looks_like_requirements(header):
        return True
    joined = " ".join(header)
    compact = _normalize_header_cell(joined)
    if any(col.replace(" ", "") in compact or col in joined for col in NON_REQUIREMENT_HEADER_COLUMNS):
        if not _header_has_any_label(header, REQ_DETAIL_LABELS):
            return False
    if not any(kw.replace(" ", "") in compact for kw in REQ_TABLE_HEADER_KEYWORDS):
        return False
    return header_has_requirement_keyword(header)


def is_administrative_table_header(header: list[str]) -> bool:
    """입찰서식·일정·제출서류 등 — 조견표 후보에서 제외."""
    non_empty = [h for h in header if h.strip()]
    if not non_empty:
        return False
    if any(len(h) >= 80 for h in non_empty):
        return False
    joined = " ".join(header)
    compact = re.sub(r"\s+", "", joined)
    if _header_triggers_table_stop(header):
        return True
    admin_hints = (
        "회사명",
        "대표자",
        "생년월일",
        "업종",
        "기관명",
        "Key Contact",
        "총자본",
        "자기자본",
        "매출원가",
        "개인(신용)정보",
        "동의서",
        "목 차",
        "목차",
    )
    return any(h in joined for h in admin_hints)


def sample_rows_look_like_requirements(sample_rows: list[list[str]] | None) -> bool:
    if not sample_rows:
        return False
    for row in sample_rows:
        joined = " ".join(row)
        if len(joined) < 12:
            continue
        if _REQ_BODY_RE.search(joined):
            return True
        if any(k in joined for k in ("제공", "구축", "제안", "개발", "지원", "연계", "구현")):
            return True
    return False


def infer_continuation_columns(texts: list[str]) -> tuple[int, int]:
    """페이지 분할 조각 표 — 헤더 없이 본문만 이어질 때 열 추정."""
    if not texts:
        return 0, 0
    detail_col = max(range(len(texts)), key=lambda i: len(texts[i]))
    category_col = 0
    for i, t in enumerate(texts):
        if i == detail_col or not t:
            continue
        if normalize_category_label(t):
            category_col = i
            break
    return category_col, detail_col


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
        if not text or not _REQ_BODY_RE.search(text):
            continue
        if len(text) >= 30:
            return True
        if text.lstrip().startswith("□") and len(text) >= 12:
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

    category = normalize_category_label(category)

    # 분류: 짧은 라벨 셀 (헤더·프로젝트명·본문 제외)
    if not category or category == detail:
        candidates: list[tuple[int, str]] = []
        for t in texts:
            if not t or t == detail:
                continue
            norm = normalize_category_label(t)
            if norm:
                candidates.append((len(norm), norm))
        if candidates:
            category = min(candidates, key=lambda x: x[0])[1]

    return category, detail


def table_looks_like_requirement_continuation(tbl: Tag) -> bool:
    """조견표 본문이 이어지는 표인지 (페이지 분할 조각)."""
    first = tbl.find("tr")
    if not isinstance(first, Tag):
        return False
    header = [c.get_text(strip=True) for c in first.find_all(["td", "th"])]
    joined = " ".join(header)
    if _header_triggers_table_stop(header):
        return False
    if header_looks_like_requirements(header):
        return True
    if first_row_is_content(header):
        return True
    return row_has_requirement_body(first.find_all(["td", "th"]))
