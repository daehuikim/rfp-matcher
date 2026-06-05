"""
표가 아닌 리스트/문단형 요구사항 추출 모듈.

opendataloader 문서를 문서 순서로 훑으며 현재 heading 컨텍스트를 추적하고,
heading 이 '요구사항 섹션'(schema.SECTION_LEXICON)일 때 그 아래 list 요소의
(중첩 포함) 항목들을 요구사항으로 수집한다. heading = category.

TOC(점선 leader)·너무 짧은 항목은 구조적으로 제외. 표 추출과 합친 뒤 dedup.
"""
from __future__ import annotations

import re

from .extract import Req
from .schema import is_requirement_section
from .text import norm, sig

# 목차 leader: "..... 4" 처럼 점선/페이지번호로 끝나는 항목
_TOC = re.compile(r"\.{4,}\s*\d*\s*$")
# 다단계 번호 하위 헤딩(예: "2.2.1 과제별 요구사항") — 번호 + 짧은 제목, 요구사항 본문 아님
_SUBHEADING = re.compile(r"^\d+(?:\.\d+){1,}\.?\s+\S.{0,12}$")
_MIN_LEN = 10


def _contents(el: dict) -> list[str]:
    """요소 내부의 모든 content 문자열을 순서대로(중첩 포함)."""
    out: list[str] = []

    def g(o):
        if isinstance(o, dict):
            c = o.get("content")
            if isinstance(c, str):
                out.append(norm(c))
            for v in o.values():
                g(v)
        elif isinstance(o, list):
            for v in o:
                g(v)

    g(el)
    return out


def _heading_text(el: dict) -> str:
    parts = _contents(el)
    return parts[0] if parts else ""


def _keep(text: str) -> bool:
    if len(text) < _MIN_LEN:
        return False
    if _TOC.search(text):
        return False
    if _SUBHEADING.match(text):  # 하위 헤딩이 리스트 항목으로 샌 것
        return False
    return True


def extract_blocks(doc_name: str, doc: dict) -> list[Req]:
    kids = doc.get("kids", [])
    out: list[Req] = []
    current_heading = ""
    for el in kids:
        t = el.get("type")
        if t == "heading":
            current_heading = _heading_text(el)
        elif t == "list":
            if not is_requirement_section(current_heading):
                continue
            for text in _contents(el):
                if _keep(text):
                    out.append(Req(
                        doc=doc_name, table_id=-1, page=el.get("page number"),
                        category=norm(current_heading), item="", detail=text,
                    ))
    return out


def dedup(reqs: list[Req]) -> list[Req]:
    seen: set[str] = set()
    out: list[Req] = []
    for r in reqs:
        k = sig(r.detail)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out
