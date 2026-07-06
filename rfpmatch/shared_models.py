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


@dataclass(init=False)
class RfpCard:
    card_id: int
    requirement: str
    part: str
    section: str
    html_excerpt: str
    page_idx: int | None = None
    anchor: str | None = None
    card_no: str | None = None
    subject: str | None = None
    category: str | None = None
    sub_subject: str | None = None
    body_fragment_level: int | None = None
    requirement_id_prefix_hint: str | None = None

    def __init__(
        self,
        card_id: int,
        requirement: str,
        part: str = "",
        section: str = "",
        html_excerpt: str = "",
        page_idx: int | None = None,
        anchor: str | None = None,
        card_no: str | None = None,
        subject: str | None = None,
        category: str | None = None,
        sub_subject: str | None = None,
        body_fragment_level: int | None = None,
        requirement_id_prefix_hint: str | None = None,
        group: str | None = None,
    ) -> None:
        self.card_id = card_id
        self.requirement = requirement
        self.part = part or (group or "")
        self.section = section
        self.html_excerpt = html_excerpt
        self.page_idx = page_idx
        self.anchor = anchor
        self.card_no = card_no
        self.subject = subject
        self.category = category
        self.sub_subject = sub_subject
        self.body_fragment_level = body_fragment_level
        self.requirement_id_prefix_hint = requirement_id_prefix_hint

    @property
    def group(self) -> str:
        return self.part
