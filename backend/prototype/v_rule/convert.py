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
