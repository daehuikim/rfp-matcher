"""
마크다운(파이프) 표 → PNG 렌더 — 법제처 표안표 등 엑셀 embed 용.
"""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_PIPE_ROW = re.compile(r"\s*\|\s*")


def _parse_pipe_table(text: str) -> tuple[list[str], list[list[str]]] | None:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    sep_i = next((i for i, ln in enumerate(lines) if "---" in ln and "|" in ln), None)
    if sep_i is None or sep_i < 1:
        return None
    headers = [_parse_pipe_row(lines[0])]
    if not headers[0]:
        return None
    rows: list[list[str]] = []
    for ln in lines[sep_i + 1 :]:
        if "|" not in ln:
            break
        row = _parse_pipe_row(ln)
        if row:
            rows.append(row)
    if not rows:
        return None
    return headers[0], rows


def _parse_pipe_row(line: str) -> list[str]:
    parts = [p.strip() for p in line.split("|")]
    if parts and not parts[0]:
        parts = parts[1:]
    if parts and not parts[-1]:
        parts = parts[:-1]
    return parts


def _table_html(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = ""
    for row in rows:
        cells = row + [""] * (len(headers) - len(row))
        body += "<tr>" + "".join(f"<td>{c}</td>" for c in cells[: len(headers)]) + "</tr>"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/>
<style>
body {{ font-family: "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; margin: 12px; }}
table {{ border-collapse: collapse; font-size: 11px; }}
th, td {{ border: 1px solid #999; padding: 6px 8px; vertical-align: top; }}
th {{ background: #305496; color: #fff; }}
td {{ max-width: 280px; word-wrap: break-word; }}
</style></head><body><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></body></html>"""


def render_pipe_table_png(text: str, out_path: Path) -> Path | None:
    """파이프 표 텍스트 → PNG. 실패 시 None."""
    parsed = _parse_pipe_table(text)
    if not parsed:
        return None
    headers, rows = parsed
    html = _table_html(headers, rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 900, "height": 600})
            page.set_content(html, wait_until="networkidle")
            page.locator("table").screenshot(path=str(out_path))
            browser.close()
        return out_path if out_path.is_file() else None
    except Exception:
        logger.warning("표 PNG 렌더 실패 — playwright", exc_info=True)
        return None


def split_detail_table_image(
    detail: str, cache_dir: Path, *, key: str
) -> tuple[str, list[str]]:
    """상세요건에서 파이프 표 분리 → (텍스트, [png paths])."""
    from .grid import _reflow_inline_pipe_table

    detail = _reflow_inline_pipe_table(detail)
    parsed = _parse_pipe_table(detail)
    if not parsed:
        return detail, []
    # 표 앞 텍스트만 남김
    lines = detail.strip().splitlines()
    sep_i = next(i for i, ln in enumerate(lines) if "---" in ln and "|" in ln)
    prefix = "\n".join(lines[: sep_i - 1]).strip() if sep_i >= 1 else ""
    table_text = "\n".join(lines[sep_i - 1 :])
    out = cache_dir / f"{key}.png"
    png = render_pipe_table_png(table_text, out)
    if png is None:
        return detail, []
    text = prefix or "[표]"
    return text, [str(png)]
