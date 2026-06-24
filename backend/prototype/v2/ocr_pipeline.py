"""스캔 PDF → Gemma VLM OCR(markdown) → Req 파싱 → 동적 finalize.

확정된 A 파이프라인(실측): pypdfium2 200DPI 렌더(전처리/crop 금지) → Gemma VLM 엄격 markdown
OCR(워터마크·타임스탬프·하단고지 제외 프롬프트). 300DPI·autocontrast는 garble/워터마크누출이라 제외.

내장 텍스트가 0자인 스캔 PDF(opendataloader 가 빈 결과를 내는)를 텍스트화해 V2 동적 경로에
태운다. OCR markdown 을 헤딩(섹션)/리스트/표/문단 단위 Req 로 **페이지순** 파싱 후 run_dynamic.finalize.
"""
from __future__ import annotations

import base64
import io
import json
import re
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.config import Settings

from .extract import Req
from .text import norm, sig

# 엄격 OCR 프롬프트 — temp/ocr 실측에서 워터마크 미누출·표보존·garble최소로 검증된 변형 A
_PROMPT = (
    "이 페이지 이미지를 **있는 그대로** 한국어 텍스트로 전사(OCR)하라.\n"
    "규칙:\n"
    "- 본문의 모든 글자·번호·기호를 빠짐없이 옮긴다. 표는 Markdown 표(| ... |)로, 목록은 '- '로 구조 유지.\n"
    "- 섹션 제목은 Markdown 제목(#, ##, ###)으로 표기한다(번호 포함, 예 '## 다. 제안요건').\n"
    "- 대각선 워터마크, 반복 보안 고지(대외비 등), 페이지 하단 타임스탬프/페이지번호 같은 배경/반복 요소는 제외.\n"
    "- 이미지에 없는 내용을 지어내지 말 것(할루시네이션 금지). 안 보이면 비운다.\n"
    "- 설명·머리말 없이 전사 결과만 출력한다."
)

# 후처리 안전망 — 워터마크/타임스탬프/페이지번호 잔재 줄 제거(프롬프트 1차 + 정규식 2차)
_NOISE_LINE = re.compile(
    r"^\s*(?:\d{1,3}|[-–—]?\s*\d{1,3}\s*[-–—]?|\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}.*|"
    r"\d{1,2}:\d{2}(:\d{2})?|대외비.*|CONFIDENTIAL.*|\d+\s*/\s*\d+)\s*$",
    re.I,
)


def _ctx(verify: bool) -> ssl.SSLContext | None:
    if verify:
        return None
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def has_text_layer(pdf: str | Path, min_chars: int = 40) -> bool:
    """PDF 에 추출 가능한 텍스트 레이어가 있나(스캔본 판정용)."""
    import fitz

    with fitz.open(str(pdf)) as d:
        total = sum(len(p.get_text().strip()) for p in d)
    return total >= min_chars


def _render(pdf: str | Path, page_idx: int, dpi: int) -> "Any":
    import pypdfium2 as pdfium
    from PIL import Image  # noqa: F401

    doc = pdfium.PdfDocument(str(pdf))
    pil = doc[page_idx].render(scale=dpi / 72.0).to_pil().convert("RGB")
    if max(pil.size) > 2200:
        r = 2200 / max(pil.size)
        pil = pil.resize((int(pil.width * r), int(pil.height * r)))
    return pil


def _b64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _ocr_image(img, s: Settings) -> str:
    body = {
        "model": s.llm_model_gemma,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(img)}"}},
        ]}],
        "max_tokens": 8000, "temperature": 0.0,
    }
    req = urllib.request.Request(
        f"{s.gemma_vlm_base_url.rstrip('/')}/chat/completions", method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer EMPTY"},
    )
    with urllib.request.urlopen(req, timeout=180, context=_ctx(s.gemma_verify_ssl)) as resp:
        out = json.loads(resp.read())
    return out["choices"][0]["message"]["content"]


def ocr_pdf_markdown(pdf: str | Path, s: Settings | None = None) -> list[tuple[int, str]]:
    """전 페이지를 Gemma VLM 로 OCR — [(page_1based, markdown)]. 병렬 호출."""
    import pypdfium2 as pdfium

    s = s or Settings()
    n = len(pdfium.PdfDocument(str(pdf)))

    def one(i: int) -> tuple[int, str]:
        try:
            return (i + 1, _ocr_image(_render(pdf, i, s.ocr_dpi), s))
        except Exception as e:  # noqa: BLE001
            return (i + 1, f"[OCR ERROR p{i + 1}: {e}]")

    with ThreadPoolExecutor(max_workers=4) as ex:
        pages = list(ex.map(one, range(n)))
    return sorted(pages, key=lambda x: x[0])


_H = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}.*$")
_LIST = re.compile(r"^\s*(?:[-*+•∙·○●❍▪]|\(?\d+[.)]|[가-힣][.)]|[ivxlcdm]+[.)]|[①-⑳])\s+")


def _clean_line(ln: str) -> str:
    return norm(ln.strip())


def _parse_markdown_to_reqs(doc_name: str, pages_md: list[tuple[int, str]]) -> list[Req]:
    """OCR markdown → Req(페이지순). 헤딩=섹션경로, 리스트/문단=상세, 표=행단위 분해."""
    out: list[Req] = []
    stack: dict[int, str] = {}

    def section_path() -> str:
        return " > ".join(stack[d] for d in sorted(stack))

    def src(page: int, kind: str) -> str:
        return f"p.{page} · {kind}"

    for page, md in pages_md:
        lines = (md or "").splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i]
            ln = raw.strip()
            if not ln or _NOISE_LINE.match(ln):
                i += 1
                continue
            mh = _H.match(ln)
            if mh:
                depth = len(mh.group(1))
                title = _clean_line(mh.group(2))
                for k in [k for k in stack if k >= depth]:
                    del stack[k]
                if title:
                    stack[depth] = title
                i += 1
                continue
            # 표 블록: 연속 '|' 줄
            if "|" in ln and ln.count("|") >= 2:
                block = []
                while i < len(lines) and lines[i].strip().count("|") >= 2:
                    block.append(lines[i].strip())
                    i += 1
                rows = [r for r in block if not _TABLE_SEP.match(r)]
                if not rows:
                    continue
                # 헤더행(첫 행) 제외, 데이터행만
                for r in rows[1:] if len(rows) > 1 else rows:
                    cells = [c.strip() for c in r.strip("|").split("|")]
                    cells = [c for c in cells if c]
                    if not cells:
                        continue
                    det = max(cells, key=len)
                    others = [c for c in cells if c is not det]
                    top = others[0] if others else ""
                    mid = others[1] if len(others) > 1 else ""
                    if len(sig(det)) < 2:
                        continue
                    out.append(Req(
                        doc=doc_name, table_id=page, page=page,
                        top=norm(top), mid=norm(mid), detail=norm(det),
                        section_path=section_path(), source=src(page, "표"),
                    ))
                continue
            # 리스트/문단 → 상세요건 한 행
            text = _clean_line(re.sub(r"^\s*[>*]\s*", "", raw))
            kind = "리스트" if _LIST.match(ln) else "문단"
            if len(sig(text)) >= 2:
                out.append(Req(
                    doc=doc_name, table_id=-1, page=page,
                    top="", mid="", detail=text,
                    section_path=section_path(), source=src(page, kind),
                ))
            i += 1
    return out


def ocr_pdf_to_reqs(pdf: str | Path, doc_name: str = "", s: Settings | None = None) -> tuple[list[Req], str]:
    """스캔 PDF → OCR → Req(페이지순) + 전체 OCR 텍스트(개요용)."""
    s = s or Settings()
    pages_md = ocr_pdf_markdown(pdf, s)
    reqs = _parse_markdown_to_reqs(doc_name or Path(pdf).stem, pages_md)
    full_text = "\n".join(md for _p, md in pages_md)
    return reqs, full_text


def run_ocr_dynamic(pdf: str | Path, gold: str | Path | None = None) -> dict[str, Any]:
    """스캔 PDF 전용 동적 경로 — OCR 추출 후 run_dynamic.finalize(섹션탭/계위/AI칼럼)."""
    from .overview import build_overview_sync
    from .run_dynamic import finalize

    reqs, full_text = ocr_pdf_to_reqs(pdf)
    steps = [f"스캔 PDF OCR(Gemma VLM 200DPI): {len(reqs)}행 추출(페이지순)"]
    overview = None
    try:
        overview = build_overview_sync(full_text, reqs)
        if overview:
            steps.append(
                f"개요 생성: 요약 + 기술 {len(overview['techs'])} + 리스크 {len(overview['risks'])}"
            )
    except Exception:  # noqa: BLE001
        pass
    res = finalize(reqs, overview, steps)
    res["report"] = {"extracted_rows": len(reqs)}
    return res
