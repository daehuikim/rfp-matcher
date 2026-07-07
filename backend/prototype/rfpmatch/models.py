"""rfpmatch 엔진 공용 데이터클래스 — TOC/섹션/카드.

rfpmatch/shared_models.py 이식. 전체 파이프라인(toc → sections → cards → rows)이
공유하는 순수 데이터클래스만 담는다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TocItem:
    level: int
    title: str
    anchor: str
    page_idx: int | None = None
    page_estimate: int | None = None


@dataclass
class Section:
    title: str
    anchor: str
    level: int
    page_idx: int | None
    html: str
    text: str


@dataclass
class RfpCard:
    card_id: int
    requirement: str
    part: str = ""
    section: str = ""
    html_excerpt: str = ""
    page_idx: int | None = None
    anchor: str | None = None
    card_no: str | None = None
    subject: str | None = None
    category: str | None = None
    sub_subject: str | None = None
    body_fragment_level: int | None = None
    requirement_id_prefix_hint: str | None = None
