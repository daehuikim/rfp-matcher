from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

import streamlit as st

from rfpmatch.app_state import load_openai_api_key
from rfpmatch.models import RfpCard


_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass
class ExcelCardRecord:
    group: str
    section: str
    subsection: str
    title: str
    description: str
    page_idx: int | None = None
    anchor: str | None = None
    level: int = 2


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", value.strip()).strip("-").lower()
    return cleaned or "section"


def _col_to_index(col: str) -> int:
    value = 0
    for ch in col:
        if not ch.isalpha():
            break
        value = value * 26 + (ord(ch.upper()) - 64)
    return value


def _safe_text_from_inline(node: ET.Element | None) -> str:
    if node is None:
        return ""
    parts: list[str] = []
    for text_node in node.iter():
        if text_node.text:
            parts.append(text_node.text)
        if text_node.tail:
            parts.append(text_node.tail)
    return _normalize_text(" ".join(parts))


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    strings: list[str] = []
    for si in root.findall(f"{_NS_MAIN}si"):
        parts: list[str] = []
        for t in si.iter(f"{_NS_MAIN}t"):
            if t.text:
                parts.append(t.text)
        strings.append(_normalize_text("".join(parts)))
    return strings


def _read_workbook_sheet_path(zf: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    first_sheet = workbook.find(f"{_NS_MAIN}sheets/{_NS_MAIN}sheet")
    if first_sheet is None:
        raise ValueError("워크북에 시트가 없습니다.")
    rel_id = first_sheet.attrib.get(f"{_NS_REL}id")
    if not rel_id:
        raise ValueError("시트 관계 ID를 찾지 못했습니다.")
    target = None
    for rel in rels.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"):
        if rel.attrib.get("Id") == rel_id:
            target = rel.attrib.get("Target")
            break
    if not target:
        raise ValueError("시트 경로를 찾지 못했습니다.")
    return target.lstrip("/")


def _read_sheet_matrix(xlsx_bytes: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r") as zf:
        shared_strings = _read_shared_strings(zf)
        sheet_path = _read_workbook_sheet_path(zf)
        sheet = ET.fromstring(zf.read(f"xl/{sheet_path}"))

        rows: dict[int, dict[int, str]] = {}
        max_col = 0
        for row in sheet.findall(f".//{_NS_MAIN}sheetData/{_NS_MAIN}row"):
            r_idx = int(row.attrib.get("r", "1"))
            row_map = rows.setdefault(r_idx, {})
            for cell in row.findall(f"{_NS_MAIN}c"):
                ref = cell.attrib.get("r", "")
                col_letters = re.match(r"([A-Z]+)", ref)
                if not col_letters:
                    continue
                c_idx = _col_to_index(col_letters.group(1))
                max_col = max(max_col, c_idx)
                cell_type = cell.attrib.get("t")
                value = ""
                if cell_type == "s":
                    v = cell.find(f"{_NS_MAIN}v")
                    if v is not None and v.text and v.text.isdigit():
                        idx = int(v.text)
                        if 0 <= idx < len(shared_strings):
                            value = shared_strings[idx]
                elif cell_type == "inlineStr":
                    value = _safe_text_from_inline(cell.find(f"{_NS_MAIN}is"))
                else:
                    v = cell.find(f"{_NS_MAIN}v")
                    if v is not None and v.text:
                        value = v.text
                row_map[c_idx] = _normalize_text(value)

        matrix: list[list[str]] = []
        for r_idx in sorted(rows):
            row = ["" for _ in range(max_col)]
            for c_idx, value in rows[r_idx].items():
                if 1 <= c_idx <= max_col:
                    row[c_idx - 1] = value
            matrix.append(row)
    return matrix


def _row_nonempty_cells(row: list[str]) -> list[tuple[int, str]]:
    return [(idx, value) for idx, value in enumerate(row) if _normalize_text(value)]


def _is_header_row(row: list[str]) -> bool:
    cells = [value.lower() for _, value in _row_nonempty_cells(row)]
    if not cells:
        return False
    hits = sum(
        1
        for cell in cells
        if any(token in cell for token in ("대분류", "중분류", "소분류", "항목", "설명", "requirement", "group", "section"))
    )
    return hits >= 2


def _parse_hierarchy_header(header_row: list[str]) -> dict[str, int]:
    aliases = {
        "group": {"대분류", "group", "그룹"},
        "section": {"중분류", "section", "섹션"},
        "subsection": {"소분류", "subsection"},
        "requirement": {"항목", "requirement", "요구사항", "title", "제목"},
        "description": {"항목 설명", "항목설명", "설명", "내용", "html_excerpt", "html"},
        "page_idx": {"페이지", "page", "page_idx"},
        "anchor": {"anchor", "앵커"},
    }
    mapping: dict[str, int] = {}
    for idx, value in _row_nonempty_cells(header_row):
        lower = _normalize_text(value).lower()
        for key, candidates in aliases.items():
            if any(candidate.lower() in lower for candidate in candidates):
                mapping[key] = idx
    return mapping


def _heading_level_from_title(value: str) -> int:
    text = _normalize_text(value)
    if re.match(r"^[IVXLCDM]+\.", text, flags=re.IGNORECASE):
        return 1
    if re.match(r"^\d+(?:\.\d+)+", text):
        return min(text.count(".") + 1, 6)
    if re.match(r"^[가나다라마바사아자차카타파하]\.", text):
        return 4
    return 2


def _bullet_level(value: str) -> int | None:
    text = _normalize_text(value)
    if not text:
        return None
    if re.match(r"^[IVXLCDM]+\.\s+", text, flags=re.IGNORECASE):
        return 1
    if re.match(r"^\d+\.\d+\.\d+", text):
        return 4
    if re.match(r"^\d+\.\d+", text):
        return 3
    if re.match(r"^\d+\)\s+", text):
        return 4
    if re.match(r"^[가나다라마바사아자차카타파하]\.\s+", text):
        return 2
    if re.match(r"^[-•·▪■◆▶]\s+", text):
        return 5
    return None


def _split_bullet_text_to_items(text: str) -> list[tuple[int, str]]:
    compact = _normalize_text(text)
    if not compact:
        return []
    pattern = re.compile(
        r"(?=(?:[IVXLCDM]+\.\s+|\d+\.\d+\.\d+\s+|\d+\.\d+\s+|\d+\)\s+|[가나다라마바사아자차카타파하]\.\s+|[-•·▪■◆▶]\s+))",
        re.IGNORECASE,
    )
    parts = [p.strip() for p in pattern.split(compact) if p.strip()]
    if len(parts) <= 1:
        return []
    items: list[tuple[int, str]] = []
    for part in parts:
        level = _bullet_level(part) or 4
        items.append((level, part))
    return items


def _bullet_level(value: str) -> int | None:
    text = _normalize_text(value)
    if not text:
        return None
    if re.match(r"^[IVXLCDM]+\.\s+", text, flags=re.IGNORECASE):
        return 1
    if re.match(r"^\d+\.\d+\.\d+", text):
        return 4
    if re.match(r"^\d+\.\d+", text):
        return 3
    if re.match(r"^\d+\)\s+", text):
        return 3
    if re.match(r"^[가나다라마바사아자차카타파하]\.\s+", text):
        return 2
    if re.match(r"^[-•·▪■◆▶]\s+", text):
        return 4
    return None


def _split_bullet_text_to_items(text: str) -> list[tuple[int, str]]:
    compact = _normalize_text(text)
    if not compact:
        return []
    pattern = re.compile(
        r"(?=(?:[IVXLCDM]+\.\s+|\d+\.\d+\.\d+\s+|\d+\.\d+\s+|\d+\)\s+|[가나다라마바사아자차카타파하]\.\s+|[-•·▪■◆▶]\s+))",
        re.IGNORECASE,
    )
    parts = [p.strip() for p in pattern.split(compact) if p.strip()]
    if len(parts) <= 1:
        return []
    items: list[tuple[int, str]] = []
    for part in parts:
        lvl = _bullet_level(part) or 4
        items.append((lvl, part))
    return items


def _escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_table_html(rows: list[list[str]]) -> str:
    table_rows: list[str] = []
    for row in rows:
        cells = "".join(f"<td>{_escape_html(cell)}</td>" for cell in row if _normalize_text(cell))
        if cells:
            table_rows.append(f"<tr>{cells}</tr>")
    return f"<table>{''.join(table_rows)}</table>" if table_rows else ""


def _load_llm_key() -> str:
    return load_openai_api_key()


def _hierarchy_rows_to_records(matrix: list[list[str]]) -> list[ExcelCardRecord]:
    if not matrix:
        return []

    # 1차: 셀 안의 불릿/번호 계층만으로 구조를 추론한다.
    bullet_records: list[ExcelCardRecord] = []
    for row in matrix:
        combined_text = " ".join(cell for _, cell in _row_nonempty_cells(row))
        bullet_items = _split_bullet_text_to_items(combined_text)
        if len(bullet_items) < 2:
            continue
        stack: list[tuple[int, str]] = []
        for level, title in bullet_items:
            while stack and stack[-1][0] >= level:
                stack.pop()
            group = stack[0][1] if stack else title
            section = stack[-1][1] if stack else title
            subsection = stack[-2][1] if len(stack) >= 2 else ""
            bullet_records.append(
                ExcelCardRecord(
                    group=_normalize_text(group),
                    section=_normalize_text(section),
                    subsection=_normalize_text(subsection),
                    title=_normalize_text(title),
                    description=_normalize_text(combined_text),
                    page_idx=None,
                    anchor=_slugify(title),
                    level=level,
                )
            )
            stack.append((level, title))
    if bullet_records:
        return bullet_records

    header_map: dict[str, int] | None = None
    for row in matrix[:8]:
        if _is_header_row(row):
            header_map = _parse_hierarchy_header(row)
            break

    records: list[ExcelCardRecord] = []
    if header_map:
        for row in matrix[1:]:
            values = _row_nonempty_cells(row)
            if not values:
                continue
            group = row[header_map["group"]] if "group" in header_map and header_map["group"] < len(row) else ""
            section = row[header_map["section"]] if "section" in header_map and header_map["section"] < len(row) else ""
            subsection = row[header_map["subsection"]] if "subsection" in header_map and header_map["subsection"] < len(row) else ""
            requirement = row[header_map["requirement"]] if "requirement" in header_map and header_map["requirement"] < len(row) else ""
            description = row[header_map["description"]] if "description" in header_map and header_map["description"] < len(row) else ""
            page_idx = None
            if "page_idx" in header_map and header_map["page_idx"] < len(row):
                raw_page = _normalize_text(row[header_map["page_idx"]])
                if raw_page.isdigit():
                    page_idx = int(raw_page) - 1 if int(raw_page) > 0 else 0
            anchor = row[header_map["anchor"]] if "anchor" in header_map and header_map["anchor"] < len(row) else ""
            title = _normalize_text(requirement or subsection or section or group or values[0][1])
            if not title:
                continue
            records.append(
                ExcelCardRecord(
                    group=_normalize_text(group or ""),
                    section=_normalize_text(section or ""),
                    subsection=_normalize_text(subsection or ""),
                    title=title,
                    description=_normalize_text(description or " ".join(cell for _, cell in values)),
                    page_idx=page_idx,
                    anchor=_slugify(anchor or title),
                    level=_heading_level_from_title(title),
                )
            )
        if records:
            return records

    # 1차 보정: 한 셀 안에 불릿 계층이 길게 붙어 있는 경우를 먼저 분해한다.
    for row in matrix:
        combined_text = " ".join(cell for _, cell in _row_nonempty_cells(row))
        bullet_items = _split_bullet_text_to_items(combined_text)
        if len(bullet_items) < 2:
            continue
        stack: list[tuple[int, str]] = []
        for level, title in bullet_items:
            while stack and stack[-1][0] >= level:
                stack.pop()
            group = stack[0][1] if stack else title
            section = stack[-1][1] if stack else title
            subsection = ""
            if len(stack) >= 2:
                subsection = stack[-1][1]
            records.append(
                ExcelCardRecord(
                    group=group,
                    section=section,
                    subsection=subsection,
                    title=title,
                    description=combined_text,
                    page_idx=None,
                    anchor=_slugify(title),
                    level=level,
                )
            )
            stack.append((level, title))
        if records:
            return records

    for row in matrix:
        values = [cell for _, cell in _row_nonempty_cells(row)]
        if not values:
            continue
        padded = values + [""] * max(0, 5 - len(values))
        group, section, subsection, item, desc = padded[:5]
        title = _normalize_text(item or subsection or section or group or desc or values[0])
        if not title:
            continue
        records.append(
            ExcelCardRecord(
                group=_normalize_text(group),
                section=_normalize_text(section),
                subsection=_normalize_text(subsection),
                title=title,
                description=_normalize_text(desc or " ".join([group, section, subsection, item, desc])),
                page_idx=None,
                anchor=_slugify(title),
                level=_heading_level_from_title(title),
            )
        )
    return records


def _records_to_cards(records: list[ExcelCardRecord]) -> list[RfpCard]:
    cards: list[RfpCard] = []
    for idx, record in enumerate(records, start=1):
        cards.append(
            RfpCard(
                card_id=idx,
                card_no=str(idx),
                requirement=record.title,
                subject=record.title,
                group=record.group or record.section or record.subsection or record.title,
                section=record.section or record.subsection or record.group or record.title,
                html_excerpt=record.description or record.title,
                page_idx=record.page_idx,
                anchor=record.anchor,
            )
        )
    return cards


def _build_cards_from_excel_bytes(xlsx_bytes: bytes) -> list[RfpCard]:
    matrix = _read_sheet_matrix(xlsx_bytes)
    records = _hierarchy_rows_to_records(matrix)
    return _records_to_cards(records)


def _records_to_rows(records: list[ExcelCardRecord]) -> list[dict]:
    rows: list[dict] = []
    for idx, record in enumerate(records, start=1):
        rows.append(
            {
                "no": idx,
                "group": record.group,
                "section": record.section,
                "subsection": record.subsection,
                "title": record.title,
                "description": record.description,
                "page_idx": record.page_idx,
                "anchor": record.anchor,
                "level": record.level,
            }
        )
    return rows


def _build_cards_from_excel_via_llm(xlsx_bytes: bytes, file_name: str) -> list[RfpCard]:
    api_key = _load_llm_key()
    if not api_key:
        raise RuntimeError("LLM API Key가 없습니다. 4단계에서 엑셀 구조가 애매하면 키를 저장한 뒤 다시 시도해 주세요.")
    try:
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("`openai` 패키지가 필요합니다. requirements 설치를 다시 실행해주세요.") from exc

    matrix = _read_sheet_matrix(xlsx_bytes)
    payload = {
        "file_name": file_name,
        "rows": matrix[:200],
        "instruction": "Analyze the spreadsheet hierarchy and return JSON array only. Each item must have level,title,group,section,html_excerpt,page_idx,anchor. Use the Excel document structure as hierarchy: 대분류 > 중분류 > 소분류 > 항목 > 항목 설명. Make card title the 항목. Use 항목 설명 as html_excerpt or content source.",
    }
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=st.session_state.get("llm_model", "gpt-4o"),
        input=[
            {"role": "system", "content": "You are an expert at converting spreadsheet hierarchies into RFP cards. Return JSON array only."},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    content = (getattr(response, "output_text", None) or "").strip()
    if not content:
        raise RuntimeError("LLM 응답이 비어 있습니다.")
    try:
        raw_items = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM 응답 JSON 파싱 실패") from exc
    if not isinstance(raw_items, list):
        raise RuntimeError("LLM 응답이 리스트 JSON 형식이 아닙니다.")

    records: list[ExcelCardRecord] = []
    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        title = _normalize_text(str(item.get("title") or item.get("requirement") or f"row {idx}"))
        if not title:
            continue
        records.append(
            ExcelCardRecord(
                group=_normalize_text(str(item.get("group") or "")),
                section=_normalize_text(str(item.get("section") or "")),
                subsection=_normalize_text(str(item.get("subsection") or "")),
                title=title,
                description=_normalize_text(str(item.get("html_excerpt") or item.get("description") or item.get("text") or "")),
                page_idx=None,
                anchor=_slugify(str(item.get("anchor") or title)),
                level=int(item.get("level") or _heading_level_from_title(title)),
            )
        )
    return _records_to_cards(records)


def _run_step4_excel(
    xlsx_bytes: bytes,
    file_name: str,
    keep_artifacts: bool,
    mark_running: Callable[[str, str], None],
    mark_done: Callable[[str, str], None],
) -> list[RfpCard]:
    mark_running("step4", "Excel에서 조견표 카드를 생성하는 중")
    try:
        cards = _build_cards_from_excel_via_llm(xlsx_bytes, file_name)
        if not cards:
            cards = _build_cards_from_excel_bytes(xlsx_bytes)
    except Exception:
        cards = _build_cards_from_excel_bytes(xlsx_bytes)
    st.session_state["cards_step4"] = cards
    if keep_artifacts:
        artifact_root = Path("artifacts") / Path(file_name or "document").stem
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "cards_from_excel.json").write_text(
            json.dumps([card.__dict__ for card in cards], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    mark_done("step4", f"Excel 조견표 카드 {len(cards)}건 생성 완료")
    return cards


def render_step4(
    keep_artifacts: bool,
    mark_running: Callable[[str, str], None],
    mark_done: Callable[[str, str], None],
    mark_error: Callable[[str, str], None],
    render_cards: Callable[[list], None],
) -> None:
    st.subheader("Excel 기반 조견표 카드 생성")
    st.write("1) 엑셀 파일의 문서 스키마를 파악하고, 2) 그 스키마를 기반으로 카드를 생성합니다.")

    uploaded = st.file_uploader("Excel 업로드", type=["xlsx"], key="step4_excel_upload")
    if uploaded is None:
        st.info("엑셀 파일을 업로드해 주세요.")
        render_cards(st.session_state.get("cards_step4") or [])
        return

    st.caption(f"업로드된 파일: {uploaded.name} | {len(uploaded.getvalue())} bytes")

    analyze_key = "step4_excel_analyze"
    generate_key = "step4_excel_generate"

    if st.button("1. 스키마 구조 파악", type="primary", use_container_width=True, key=analyze_key):
        try:
            matrix = _read_sheet_matrix(uploaded.getvalue())
            records = _hierarchy_rows_to_records(matrix)
            st.session_state["excel_hierarchy_records"] = records
            st.session_state["excel_hierarchy_rows"] = _records_to_rows(records)
            st.success(f"스키마 구조 {len(records)}건을 파악했습니다.")
        except Exception as exc:  # noqa: BLE001
            st.session_state["excel_hierarchy_records"] = None
            st.session_state["excel_hierarchy_rows"] = None
            mark_error("step4", str(exc))
            st.exception(exc)

    hierarchy_rows = st.session_state.get("excel_hierarchy_rows") or []
    if hierarchy_rows:
        st.markdown("**스키마 구조 결과**")
        st.dataframe(hierarchy_rows, use_container_width=True, hide_index=True)

    if st.button("2. 스키마에 맞게 카드 생성", use_container_width=True, key=generate_key):
        try:
            records = st.session_state.get("excel_hierarchy_records")
            if not records:
                matrix = _read_sheet_matrix(uploaded.getvalue())
                records = _hierarchy_rows_to_records(matrix)
                st.session_state["excel_hierarchy_records"] = records
                st.session_state["excel_hierarchy_rows"] = _records_to_rows(records)
            cards = _records_to_cards(records)
            st.session_state["cards_step4"] = cards
            if keep_artifacts:
                artifact_root = Path("artifacts") / Path(uploaded.name or "document").stem
                artifact_root.mkdir(parents=True, exist_ok=True)
                (artifact_root / "cards_from_excel.json").write_text(
                    json.dumps([card.__dict__ for card in cards], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            mark_done("step4", f"Excel 조견표 카드 {len(cards)}건 생성 완료")
            st.success(f"Excel 조견표 카드 {len(cards)}건을 생성했습니다.")
        except Exception as exc:  # noqa: BLE001
            mark_error("step4", str(exc))
            st.exception(exc)

    cards = st.session_state.get("cards_step4") or []
    render_cards(cards)
