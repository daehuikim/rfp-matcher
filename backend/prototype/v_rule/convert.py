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
    out: list[str] = ["<html><body>"]
    tbl: list[tuple[list[str], str]] = []   # (cells, 원문 줄)

    def flush_tbl():
        nonlocal tbl
        if not tbl:
            return
        # 진짜 표 = 파이프 행 2개 이상. 외톨이 파이프 한 줄(다이어그램 라벨 'A | B | C')만
        # 문단으로 보존 — 표를 통째로 문단화하면 진짜 요구표(구분|상세내역|요구수준)까지
        # 깨진다(스캔 신한카드 실측). 열수 비일관은 그대로 둠(_col_stats 가 ragged 허용).
        if len(tbl) >= 2 and max(len(c) for c, _ in tbl) >= 2:
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
        if tbl:
            # 표 진행 중 파이프 없는 줄 = VLM 이 긴 셀을 줄바꿈한 랩핑 → 직전 행 마지막
            # 셀에 이어붙임(표를 1행 조각들로 파편화시키던 원인). 줄 경계는 \n 으로 보존해
            # 셀 내 항목 분해(split_items)가 작동하게 한다.
            cells, rawln = tbl[-1]
            if cells:
                cells[-1] = (cells[-1] + "\n" + ln).strip()
            tbl[-1] = (cells, rawln + " " + ln)
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


def _flatten_json_text_items(kids: list[dict]) -> list[tuple[str, int]]:
    """opendataloader JSON kids → (정규화텍스트, page) 문서순 리스트(표 셀 안 문단도 흡수)."""
    out: list[tuple[str, int]] = []

    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    def walk(node: dict) -> None:
        if node.get("type") == "table":
            for row in node.get("rows", []) or []:
                for cell in row.get("cells", []) or []:
                    for k in cell.get("kids", []) or []:
                        walk(k)
            return
        content = node.get("content")
        pg = node.get("page number")
        if content and pg:
            try:
                out.append((_norm(content), int(pg)))
            except (TypeError, ValueError):
                pass
        for k in node.get("kids", []) or []:
            walk(k)

    for k in kids:
        walk(k)
    return out


def _flatten_json_tables(kids: list[dict]) -> list[tuple[str, int]]:
    """표 노드 → (머리텍스트 정규화 ≤80자, page) 문서순 리스트.

    표는 순번만으로 HTML 과 정렬하면 안 된다 — opendataloader 가 여러 페이지에 걸친
    표를 JSON 에선 1노드로 합치고 HTML 에선 페이지별 <table> 조각으로 쪼개서(기아 실측
    JSON 179 vs HTML 266) 순번이 누적으로 밀려 수십 페이지 오차가 났다. 내용(머리텍스트)
    기반 매칭용으로 각 표의 앞부분 텍스트를 함께 뽑는다.
    """
    out: list[tuple[str, int]] = []

    def _head_text(node: dict) -> str:
        parts: list[str] = []

        def walk_txt(n: dict) -> None:
            if sum(len(p) for p in parts) >= 80:
                return
            c = n.get("content")
            if c:
                parts.append(re.sub(r"\s+", "", c))
            for row in n.get("rows", []) or []:
                for cell in row.get("cells", []) or []:
                    for k in cell.get("kids", []) or []:
                        walk_txt(k)
            for k in n.get("kids", []) or []:
                walk_txt(k)

        walk_txt(node)
        return "".join(parts)[:80]

    def walk(node: dict) -> None:
        if node.get("type") == "table":
            pg = node.get("page number")
            try:
                out.append((_head_text(node), int(pg)))
            except (TypeError, ValueError):
                pass
            return
        for k in node.get("kids", []) or []:
            walk(k)

    for k in kids:
        walk(k)
    return out


def _inject_page_data(html: str, json_path: Path) -> str:
    """opendataloader JSON 의 page number 를 HTML 요소에 data-page 속성으로 역주입.

    HTML 출력엔 페이지 경계 표식이 전혀 없다(grep 실측 0건) — 반면 JSON 의 모든 요소엔
    'page number'가 있다. 같은 문서를 같은 도구가 만든 두 출력물이라 **문서순 텍스트
    매칭**으로 정렬한다(완전일치 실패 시 포함관계로 재시도, 그마저 실패하면 직전 매치
    페이지를 이어써 최소한 근사값은 남긴다 — 표 구조 차이로 100% 정확할 순 없지만
    "몇 페이지 근처"를 찾는 원문뷰어 용도로는 충분).
    """
    import json as _json
    try:
        data = _json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return html
    kids = data.get("kids") or []
    text_items = _flatten_json_text_items(kids)
    json_tables = _flatten_json_tables(kids)
    if not text_items and not json_tables:
        return html

    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    ptr = 0
    last_page = json_tables[0][1] if json_tables else (text_items[0][1] if text_items else None)
    WINDOW = 60
    for el in body.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"]):
        if el.name != "table" and el.find_parent("table"):
            continue
        if el.name == "table":
            if el.find_parent("table") is not None:
                continue
            # 표도 순번이 아니라 **텍스트 앵커**(전방 포인터)로 정렬한다. 순번 매칭은
            # opendataloader 가 여러 페이지 표를 JSON 1노드 vs HTML 페이지별 조각으로
            # 다르게 쪼개(기아 실측 179 vs 266) 수십 페이지 드리프트를 만들었다.
            # JSON 의 셀 문단 조각(jt)이 이 표의 셀 전체 텍스트(ctext) 안에 포함되는지로
            # 매칭 — 같은 내용의 표가 여러 벌 있어도(p25/p27 사본) 문서순 전방 탐색이라
            # 등장 순서대로 올바른 사본에 붙는다.
            cand = None
            tried = 0
            for td in el.find_all(["td", "th"]):
                ctext = _norm(td.get_text(" "))
                if len(ctext) < 8:
                    continue
                tried += 1
                if tried > 8:
                    break
                for j in range(ptr, min(ptr + 500, len(text_items))):
                    jt, jp = text_items[j]
                    if jt and len(jt) >= 6 and (jt in ctext or ctext in jt):
                        cand = (j, jp)
                        break
                if cand:
                    break
            if cand is not None:
                ptr = cand[0] + 1
                last_page = cand[1]
                el["data-page"] = str(cand[1])
            elif last_page is not None:
                el["data-page"] = str(last_page)
            continue
        text = _norm(el.get_text(" "))
        if not text:
            continue
        found = None
        for j in range(ptr, min(ptr + WINDOW, len(text_items))):
            jt, jp = text_items[j]
            if jt and (jt == text or jt in text or text in jt):
                found = (j, jp)
                break
        if found:
            ptr = found[0] + 1
            last_page = found[1]
            el["data-page"] = str(found[1])
        elif last_page is not None:
            el["data-page"] = str(last_page)
    return str(soup)


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
        if json_p:
            # 페이지 번호를 HTML에 역주입 — v_rule 추출(Req.page)과 "원문뷰어" 양쪽이
            # 같은 파일을 읽으므로 한 번의 주입으로 둘 다 페이지 정보를 얻는다.
            try:
                injected = _inject_page_data(
                    html_p.read_text(encoding="utf-8", errors="replace"), json_p)
                html_p.write_text(injected, encoding="utf-8")
            except Exception:
                pass
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
