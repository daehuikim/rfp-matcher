"""스펙 1단계 — PDF →(OpenDataLoader 로컬)→ HTML/Markdown, HTML→TXT.

OpenDataLoader 오픈소스 s/w 를 로컬에서 호출(html parser)해 html·markdown 파일 생성,
html 파일로부터 txt 파일 생성. (스펙: "pdf to html, markdown, txt 에 집중")
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from bs4 import BeautifulSoup

_OPENJDK = "/opt/homebrew/opt/openjdk@17/bin"


def _ensure_java() -> None:
    if Path(_OPENJDK, "java").exists():
        os.environ["PATH"] = f"{_OPENJDK}:{os.environ.get('PATH', '')}"
        home = Path(_OPENJDK).parent / "libexec" / "openjdk.jdk" / "Contents" / "Home"
        if home.exists():
            os.environ["JAVA_HOME"] = str(home)


def _html_to_txt(html: str) -> str:
    """HTML 본문 → 평문 txt (태그 제거, 표는 행/셀 구분 보존). 목차 생성 LLM 입력용."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["style", "script"]):
        tag.decompose()
    lines: list[str] = []
    body = soup.body or soup
    for el in body.descendants:
        name = getattr(el, "name", None)
        if name in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li"):
            t = el.get_text(" ", strip=True)
            if t:
                lines.append(t)
        elif name == "tr":
            cells = [c.get_text(" ", strip=True) for c in el.find_all(["td", "th"], recursive=False)]
            row = " | ".join(c for c in cells if c)
            if row:
                lines.append(row)
    txt = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", txt)


def _markdown_to_html(md: str) -> str:
    """OCR markdown → 최소 HTML. 파이프 표는 <table>, 나머지 줄은 <p>(카드 마커는 텍스트로 유지).

    가짜 표 방지: VLM 이 다이어그램/그림을 'A | B | C' 한 줄로 뱉는 경우가 있어, 파이프
    줄이 모였을 때 **진짜 표 형태(데이터행 ≥2 + 최빈 열수 ≥2 + 열수 일관성 ≥0.8)**일 때만
    <table> 로 방출하고, 아니면 각 줄을 원문 그대로 <p> 로 남긴다(내용 소실 없음).
    """
    import html as _h
    from collections import Counter
    out: list[str] = ["<html><body>"]
    tbl: list[tuple[list[str], str]] = []   # (cells, 원문 줄)

    def flush_tbl():
        nonlocal tbl
        if not tbl:
            return
        counts = Counter(len(cells) for cells, _ in tbl)
        modal_cols, modal_n = counts.most_common(1)[0]
        is_table = (len(tbl) >= 2 and modal_cols >= 2
                    and modal_n / len(tbl) >= 0.8)
        if is_table:
            out.append("<table>")
            for cells, _ in tbl:
                out.append("<tr>" + "".join(f"<td>{_h.escape(c)}</td>" for c in cells) + "</tr>")
            out.append("</table>")
        else:
            for _, raw_ln in tbl:            # 표 아님(다이어그램 파편 등) → 원문 보존
                out.append(f"<p>{_h.escape(raw_ln)}</p>")
        tbl = []

    for raw in md.splitlines():
        ln = raw.strip()
        if not ln or set(ln) <= set("-|: "):   # 표 구분선/빈줄
            if "|" not in ln:
                flush_tbl()
            continue
        if ln.count("|") >= 2:                   # 파이프 표 행 후보
            tbl.append(([c.strip() for c in ln.strip("|").split("|")], ln))
            continue
        flush_tbl()
        t = re.sub(r"^#+\s*", "", ln)            # markdown heading 기호 제거(제목 텍스트 유지)
        out.append(f"<p>{_h.escape(t)}</p>")
    flush_tbl()
    out.append("</body></html>")
    return "\n".join(out)


def convert_any(src_path: str | Path, workdir: str | Path) -> dict[str, Path]:
    """입력 타입 분기 — PDF=OpenDataLoader, HWP/HWPX/DOC(X)=앱 변환기→HTML. 범용 진입점.

    HWP 계열은 OpenDataLoader 가 PDF 전용이라 기존 변환기(hwp5html/hwpx/libreoffice)로 HTML 화 후
    동일한 v_rule HTML 파이프라인을 탄다. (스펙은 PDF 중심이나 범용 비교 위해 한글도 지원)
    """
    src = Path(src_path)
    ext = src.suffix.lower()
    if ext == ".pdf":
        # 자동 감지(파일명 무관): 텍스트 레이어 없으면 스캔본 → Gemma VLM OCR → HTML
        from prototype.v2.ocr_pipeline import has_text_layer
        if not has_text_layer(src):
            from prototype.v2.ocr_pipeline import ocr_pdf_markdown
            work = Path(workdir); work.mkdir(parents=True, exist_ok=True)
            pages = ocr_pdf_markdown(src)
            html = _markdown_to_html("\n".join(m for _, m in pages))
            html_p = work / f"{src.stem}.html"
            html_p.write_text(html, encoding="utf-8")
            txt_p = work / f"{src.stem}.txt"; txt_p.write_text(_html_to_txt(html), encoding="utf-8")
            return {"html": html_p, "txt": txt_p, "ocr": html_p}
        return convert_pdf(src, workdir)

    import asyncio
    import sys
    os.environ["PATH"] = str(Path(sys.executable).parent) + ":" + os.environ.get("PATH", "")
    from app.core.config import Settings
    from app.domain.enums import DocumentMime
    from app.domain.models import Document
    from app.phase1.converters.registry import select_converter

    mime = {
        ".hwp": DocumentMime.HWP, ".hwpx": DocumentMime.HWPX,
        ".doc": DocumentMime.DOC, ".docx": DocumentMime.DOCX,
        ".html": DocumentMime.HTML, ".htm": DocumentMime.HTML,
    }[ext]
    work = Path(workdir); work.mkdir(parents=True, exist_ok=True)
    conv = select_converter(mime, Settings())
    doc = Document(id="vrule", src_path=src, mime=mime)
    html_doc = asyncio.run(conv.convert(doc, work))
    html_p = work / f"{src.stem}.html"
    if Path(html_doc.html_path) != html_p:
        html_p.write_bytes(Path(html_doc.html_path).read_bytes())
    txt = _html_to_txt(html_p.read_text(encoding="utf-8", errors="replace"))
    txt_p = work / f"{src.stem}.txt"; txt_p.write_text(txt, encoding="utf-8")
    return {"html": html_p, "txt": txt_p}


def convert_pdf(pdf_path: str | Path, workdir: str | Path) -> dict[str, Path]:
    """PDF → {html, markdown, txt, json} 파일 경로 dict. OpenDataLoader 로컬 변환."""
    _ensure_java()
    import opendataloader_pdf

    src = Path(pdf_path)
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    for stale in list(work.glob("*.json")) + list(work.glob("*.html")) + list(work.glob("*.md")):
        stale.unlink()

    opendataloader_pdf.convert(
        input_path=[str(src)], output_dir=str(work),
        format=["html", "markdown", "json"],
    )

    def _find(*exts: str) -> Path | None:
        reserved = {"manifest.json", "report.json"}
        for p in sorted(work.glob("*")):
            if p.suffix.lower() in exts and p.name not in reserved:
                return p
        return None

    html_p = _find(".html")
    md_p = _find(".md", ".markdown")
    json_p = _find(".json")
    out: dict[str, Path] = {}
    if html_p:
        out["html"] = html_p
        txt = _html_to_txt(html_p.read_text(encoding="utf-8", errors="replace"))
        txt_p = work / f"{src.stem}.txt"
        txt_p.write_text(txt, encoding="utf-8")
        out["txt"] = txt_p
    if md_p:
        out["markdown"] = md_p
    if json_p:
        out["json"] = json_p
    return out
