"""파생 뷰어 PDF(preview.pdf) 생성 — 비-PDF 원본(HWP/DOCX 등)도 페이지 단위 PDF 로.

- DOCX/DOC/PPTX 등 LibreOffice 가 여는 포맷: soffice --convert-to pdf (원본 레이아웃
  페이지네이션, 최고 품질)
- HWP/HWPX 등 나머지: 추출 단계가 만든 변환 HTML → Chromium page.pdf()
  (macOS LibreOffice 는 .hwp 로드 불가 — hwp5html HTML 을 인쇄 페이지네이션으로 굽는다)

생성된 PDF 는 (1) FE 원문 뷰어가 그대로 표시하고 (2) 페이지 오라클
(assign_pages_from_pdf)의 좌표계가 되므로, 뷰어 페이지와 행별 source_page 가 구성상
항상 일치한다. content_hash 캐시에 1회 생성·고정 — 이후 행 삭제/편집이 있어도
페이지네이션이 변하지 않아 원문 트래킹이 안정적이다.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_SOFFICE_EXTS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".rtf"}

# Chromium 인쇄용 최소 스타일 — CDN @import 금지(오프라인/인쇄 시 네트워크 대기 유발),
# 로컬 폰트 스택만. 변환 HTML(hwp5html 등)은 CSS 가 없어 기본 명조로 인쇄되는 것 방지.
_PRINT_STYLE = """
<style id="rfp-print-style">
html, body { font-family: "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR",
  sans-serif; font-size: 13px; line-height: 1.6; color: #111; margin: 0; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 12px;
  page-break-inside: auto; }
td, th { border: 1px solid #999; padding: 4px 7px; vertical-align: top; }
th { background: #f0f0f0; }
img { max-width: 100%; height: auto; }
</style>
"""


def _inject_print_style(html: str) -> str:
    low = html.lower()
    idx = low.find("</head>")
    if idx != -1:
        return html[:idx] + _PRINT_STYLE + html[idx:]
    idx = low.find("<body")
    if idx != -1:
        gt = html.find(">", idx)
        if gt != -1:
            return html[: gt + 1] + _PRINT_STYLE + html[gt + 1:]
    return _PRINT_STYLE + html


def _soffice_pdf(src: Path, out_dir: Path) -> Path | None:
    from app.phase1.converters.libreoffice_paths import resolve_soffice_bin

    try:
        soffice = resolve_soffice_bin(None)
    except FileNotFoundError:
        return None
    profile = f"file://{(out_dir / 'lo_profile').resolve()}"
    try:
        proc = subprocess.run(
            [soffice, "--headless", f"-env:UserInstallation={profile}",
             "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
            capture_output=True, timeout=180,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("view_pdf soffice 실패 %s: %s", src.name, exc)
        return None
    if proc.returncode != 0:
        logger.info("view_pdf soffice rc=%s: %s", proc.returncode,
                    proc.stderr.decode("utf-8", errors="replace")[:200])
        return None
    produced = out_dir / f"{src.stem}.pdf"
    return produced if produced.is_file() else None


def _chromium_pdf(html_path: Path, out: Path) -> bool:
    """변환 HTML → A4 인쇄 PDF. 상대경로 리소스가 살도록 스타일 사본을 같은 폴더에 둔다."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    styled = html_path.with_name(f"{html_path.stem}_print.html")
    try:
        styled.write_text(
            _inject_print_style(html_path.read_text(encoding="utf-8", errors="replace")),
            encoding="utf-8",
        )
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(styled.as_uri(), wait_until="load", timeout=180_000)
                page.pdf(path=str(out), format="A4", print_background=True,
                         margin={"top": "12mm", "bottom": "12mm",
                                 "left": "10mm", "right": "10mm"})
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 — 뷰어 파생물은 실패해도 추출을 막지 않는다
        logger.warning("view_pdf chromium 실패 %s: %s", html_path.name, exc)
        return False
    finally:
        styled.unlink(missing_ok=True)
    return out.is_file()


def ensure_view_pdf(src_path: Path, converted_html: Path | None,
                    bucket: Path | None, fallback_dir: Path) -> Path | None:
    """뷰어/오라클용 PDF 확보. PDF 원본이면 원본 그대로, 비-PDF 는 파생 생성(캐시 우선).

    bucket(content_hash 캐시)이 있으면 거기에 preview.pdf 로 저장 — documents.py 의
    _ensure_preview_pdf 와 같은 파일명 컨벤션이라 어느 쪽이 먼저 만들어도 공유된다.
    """
    src = Path(src_path)
    if src.suffix.lower() == ".pdf":
        return src if src.is_file() else None
    out_dir = bucket if bucket is not None else fallback_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "preview.pdf"
    if out.is_file():
        return out
    if src.suffix.lower() in _SOFFICE_EXTS and src.is_file():
        produced = _soffice_pdf(src, out_dir)
        if produced is not None:
            if produced != out:
                produced.replace(out)
            return out
    if converted_html is not None and Path(converted_html).is_file():
        if _chromium_pdf(Path(converted_html), out):
            return out
    return None
