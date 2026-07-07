from __future__ import annotations

import base64
import html as htmllib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from rfpmatch.app_state import load_openai_api_key
from rfpmatch.shared_llm import estimate_llm_cost_usd, extract_token_usage, responses_create_kwargs


@dataclass
class TableCandidate:
    index: int
    html: str
    text: str
    row_count: int
    col_counts: list[int]
    rowspan_cells: int
    colspan_cells: int
    suspicion_score: float
    page_number: int | None = None
    page_match_score: float = 0.0
    page_image_data_uri: str = ""
    vlm_result: dict | None = None


def _clean_text(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _load_fitz() -> Any:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "PyMuPDF(`fitz`)가 설치되지 않았습니다. VLM 비교 모드를 사용하려면 `pip install pymupdf`를 실행하세요."
        ) from exc
    return fitz


def has_pymupdf() -> bool:
    try:
        _load_fitz()
    except RuntimeError:
        return False
    return True


def _table_signature(table: Tag) -> tuple[list[int], int, int, str]:
    rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    col_counts: list[int] = []
    rowspan_cells = 0
    colspan_cells = 0
    for row in rows:
        cells = row.find_all(["td", "th"], recursive=False)
        col_counts.append(len(cells))
        for cell in cells:
            try:
                rowspan = int(cell.get("rowspan", 1) or 1)
            except (TypeError, ValueError):
                rowspan = 1
            try:
                colspan = int(cell.get("colspan", 1) or 1)
            except (TypeError, ValueError):
                colspan = 1
            if rowspan > 1:
                rowspan_cells += 1
            if colspan > 1:
                colspan_cells += 1
    text = _clean_text(table.get_text(" ", strip=True))
    return col_counts, rowspan_cells, colspan_cells, text


def _table_suspicion_score(table: Tag) -> float:
    rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    if not rows:
        return 0.0
    col_counts, rowspan_cells, colspan_cells, text = _table_signature(table)
    score = 0.0
    if len(rows) >= 2 and len(set(col_counts)) > 1:
        score += 2.0
    if any(count <= 1 for count in col_counts[1:]):
        score += 1.5
    if rowspan_cells or colspan_cells:
        score += min(2.0, 0.5 * (rowspan_cells + colspan_cells))
    if len(rows) >= 6:
        score += 0.5
    if text and len(text) < 30:
        score += 0.5
    if any(len(tr.find_all(["td", "th"], recursive=False)) == 0 for tr in rows):
        score += 1.0
    return score


def find_suspicious_table_candidates(html: str, limit: int = 5) -> list[TableCandidate]:
    if not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[TableCandidate] = []
    for idx, table in enumerate(soup.find_all("table"), start=1):
        if table.find_parent("table") is not None:
            continue
        col_counts, rowspan_cells, colspan_cells, text = _table_signature(table)
        score = _table_suspicion_score(table)
        candidates.append(
            TableCandidate(
                index=idx,
                html=str(table),
                text=text,
                row_count=len([tr for tr in table.find_all("tr") if tr.find_parent("table") is table]),
                col_counts=col_counts,
                rowspan_cells=rowspan_cells,
                colspan_cells=colspan_cells,
                suspicion_score=score,
            )
        )
    candidates.sort(key=lambda item: (item.suspicion_score, item.row_count, len(item.text)), reverse=True)
    return candidates[:limit]


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _text_similarity(a: str, b: str) -> float:
    left = _normalize_for_match(a)
    right = _normalize_for_match(b)
    if not left or not right:
        return 0.0
    ratio = SequenceMatcher(None, left[:6000], right[:12000]).ratio()
    left_tokens = set(re.findall(r"[0-9A-Za-z가-힣]+", left))
    right_tokens = set(re.findall(r"[0-9A-Za-z가-힣]+", right))
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens), 1)
    return (0.7 * ratio) + (0.3 * overlap)


def match_candidate_to_pdf_page(pdf_path: Path, candidate: TableCandidate) -> TableCandidate:
    if not pdf_path.exists():
        return candidate
    fitz = _load_fitz()
    best_page = None
    best_score = 0.0
    with fitz.open(pdf_path) as doc:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            page_text = page.get_text("text", sort=True)
            score = _text_similarity(candidate.text, page_text)
            if score > best_score:
                best_page = page_index + 1
                best_score = score
    candidate.page_number = best_page
    candidate.page_match_score = best_score
    if best_page is not None:
        with fitz.open(pdf_path) as doc:
            page = doc.load_page(best_page - 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            png_bytes = pix.tobytes("png")
            candidate.page_image_data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
    else:
        candidate.page_image_data_uri = ""
    return candidate


def candidate_to_preview_rows(candidate: TableCandidate) -> list[dict]:
    rows = [tr for tr in BeautifulSoup(candidate.html, "html.parser").find_all("tr")]
    preview: list[dict] = []
    for row in rows[:12]:
        cells = row.find_all(["td", "th"], recursive=False)
        preview.append(
            {
                "cells": len(cells),
                "text": " | ".join(_clean_text(cell.get_text(" ", strip=True)) for cell in cells if _clean_text(cell.get_text(" ", strip=True))),
            }
        )
    return preview


def _parse_json_loose(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("VLM 응답 JSON 파싱 실패")


def rows_to_html_table(columns: list[str], rows: list[list[str]]) -> str:
    max_cols = max([len(columns), *[len(row) for row in rows]], default=0)
    if max_cols == 0:
        return "<table border=\"1\"></table>"

    def _pad(values: list[str]) -> list[str]:
        padded = list(values[:max_cols])
        padded.extend([""] * (max_cols - len(padded)))
        return padded

    html_rows: list[str] = []
    if columns:
        header = "".join(f"<th>{htmllib.escape(col)}</th>" for col in _pad(columns))
        html_rows.append(f"<tr>{header}</tr>")
    for row in rows:
        cells = "".join(f"<td>{htmllib.escape(cell)}</td>" for cell in _pad([str(cell) for cell in row]))
        html_rows.append(f"<tr>{cells}</tr>")
    return f"<table border=\"1\">{''.join(html_rows)}</table>"


def review_candidate_with_vlm(
    candidate: TableCandidate,
    *,
    model_name: str,
    pdf_path: Path,
) -> tuple[TableCandidate, dict]:
    api_key = load_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다.")
    fitz = _load_fitz()
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("`openai` 패키지가 필요합니다. requirements 설치를 다시 실행해주세요.") from exc

    candidate = match_candidate_to_pdf_page(pdf_path, candidate)
    if not candidate.page_image_data_uri:
        raise RuntimeError("PDF 페이지 이미지를 만들 수 없습니다.")

    client = OpenAI(api_key=api_key)
    system_prompt = (
        "You reconstruct a single PDF table from a page image. "
        "Compare the visible table in the image with the provided HTML excerpt, but trust the image when they disagree. "
        "Return JSON only with keys: table_title(str), confidence(str one of high|medium|low), columns(list of strings), rows(list of list of strings), notes(str). "
        "Do not invent missing content. "
        "If merged cells are visible, repeat the cell text in the affected rows/columns rather than guessing hidden structure. "
        "Keep the output limited to the single table visible on the page."
    )
    user_prompt = (
        "Reconstruct the suspicious table from this PDF page image.\n"
        f"[PAGE_NUMBER] {candidate.page_number or ''}\n"
        f"[ODL_TABLE_EXCERPT]\n{candidate.html}\n"
        f"[ODL_TABLE_TEXT]\n{candidate.text}\n"
        "Return JSON only."
    )
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_prompt},
                    {"type": "input_image", "image_url": candidate.page_image_data_uri, "detail": "high"},
                ],
            }
        ],
        **responses_create_kwargs(model_name),
    )
    parsed = _parse_json_loose((getattr(response, "output_text", "") or "").strip())
    columns = [str(col).strip() for col in parsed.get("columns", []) if str(col).strip()]
    rows: list[list[str]] = []
    for row in parsed.get("rows", []):
        if isinstance(row, list):
            rows.append([str(cell).strip() for cell in row])
    table_html = rows_to_html_table(columns, rows)
    result = {
        "table_title": str(parsed.get("table_title", "")).strip(),
        "confidence": str(parsed.get("confidence", "medium")).strip() or "medium",
        "columns": columns,
        "rows": rows,
        "notes": str(parsed.get("notes", "")).strip(),
        "table_html": table_html,
    }
    input_tokens, output_tokens = extract_token_usage(response)
    usage_info = {
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": estimate_llm_cost_usd(model_name, input_tokens, output_tokens),
    }
    candidate.vlm_result = result
    return candidate, usage_info
