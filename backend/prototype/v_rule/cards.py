"""HTML → 문서순 블록 → **가/나 등 마커 기반 카드 유닛** 분할 (전처리 포함).

비정형 입력(무엇이든)이 HTML 로 바뀐 뒤, hwp5html 처럼 heading/list 태그가 없어도
**텍스트 선두 마커**(로마자/숫자/한글 가·나/ㄱ·ㄴ/1)·2)/❍·-)로 계위를 읽어 카드로 자른다.
카드 = '가. xxx요구사항' 같은 유닛 + 그 아래 본문/표. 하드코딩·키워드 룰 없음(마커 형태만).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

# ── 마커 계위 사다리(형태만; 문서 도메인 무관) ──────────────────────────────
# level 작을수록 상위. 같은 문서 안에서 상대 계위로 카드 경계를 잡는다.
# 유니코드 로마자(Ⅰ-Ⅻ, hwp5html 이 표 셀에 쓰는 형태) + ASCII 로마자 모두 지원. 점 선택(표행은 점 없음).
_ROMAN = r"(?:[IVXLCDM]{1,4}|[Ⅰ-Ⅺ])"
_MARKERS: list[tuple[int, "re.Pattern[str]"]] = [
    (0, re.compile(rf"^\s*(?:제\s*\d+\s*[부편]|{_ROMAN}\.?)\s")),           # 제1부 / I. / Ⅰ (점 선택)
    (1, re.compile(r"^\s*(?:제\s*\d+\s*장|\d+\.)(?!\d)\s")),                # 제1장 / 1.
    (2, re.compile(r"^\s*\d+\.\d+(?!\.\d)\s")),                            # 1.1
    (2, re.compile(r"^\s*[가-힣]\.\s")),                                    # 가. 나.
    (3, re.compile(r"^\s*\d+\.\d+\.\d+\s")),                               # 1.1.1
    (3, re.compile(r"^\s*[ㄱ-ㅎ]\.\s")),                                    # ㄱ. ㄴ.
    (4, re.compile(r"^\s*(?:\d+\)|[가-힣]\)|\([0-9가-힣]+\)|[①-⑳])\s?")),   # 1) 가) (1) ①
    (5, re.compile(r"^\s*[❍○●◦▪◆■□∙·*※\-–—]\s")),                          # 불릿(말단)
]
_MARK_ANY = re.compile(rf"^\s*(?:제\s*\d+\s*[부편장]|{_ROMAN}\.?|\d+(?:\.\d+)*\.?|"
                       r"[가-힣]\.|[ㄱ-ㅎ]\.|\d+\)|[가-힣]\)|\([0-9가-힣]+\)|[①-⑳]|[❍○●◦▪◆■□∙·*※\-–—])\s?")


def marker_level(text: str) -> int | None:
    """텍스트 선두 마커의 계위(0=최상위). 마커 없으면 None."""
    t = (text or "").lstrip()
    for lvl, rx in _MARKERS:
        if rx.match(t):
            return lvl
    return None


def strip_marker(text: str) -> str:
    return _MARK_ANY.sub("", (text or "").strip(), count=1).strip()


@dataclass
class Block:
    kind: str            # "text" | "table"
    text: str = ""       # text 블록 원문(마커 포함)
    grid: list[list[str]] = field(default_factory=list)  # table 블록


@dataclass
class Card:
    marker: str          # 선두 마커(가. / 1. / ❍ ...)
    title: str           # 마커 뗀 제목
    level: int
    blocks: list[Block] = field(default_factory=list)   # 카드 본문(text/table)

    @property
    def has_table(self) -> bool:
        return any(b.kind == "table" for b in self.blocks)

    @property
    def text_lines(self) -> list[str]:
        return [b.text for b in self.blocks if b.kind == "text"]


def _cell(td: Tag) -> str:
    return re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()


def _table_grid(tbl: Tag) -> list[list[str]]:
    rows = []
    for tr in tbl.find_all("tr"):
        if tr.find_parent("table") is not tbl:
            continue
        rows.append([_cell(td) for td in tr.find_all(["td", "th"], recursive=False)])
    return rows


def iter_blocks(html: str) -> list[Block]:
    """문서순 블록(text/table). 전처리: p/td 중복·빈 블록·연속중복 제거(hwp5html 잡음)."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    out: list[Block] = []
    seen_recent: list[str] = []
    for el in body.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"]):
        if el.name != "table" and el.find_parent("table"):
            continue                       # 표 안 텍스트는 table 블록으로만
        if el.name == "table":
            if el.find_parent("table") is not None:
                continue
            grid = _table_grid(el)
            if any(c.strip() for r in grid for c in r):
                out.append(Block(kind="table", grid=grid))
                seen_recent = []
            continue
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if not t:
            continue
        if t in seen_recent:               # 직전 몇 블록과 동일 텍스트 → hwp5html 중복 제거
            continue
        out.append(Block(kind="text", text=t))
        seen_recent = ([t] + seen_recent)[:3]
    return out


def segment_cards(html: str) -> list[Card]:
    """블록 → 카드. 카드 경계 = '카드 레벨' 마커(가/나 우선, 없으면 최빈 상위 숫자레벨).

    카드 레벨은 문서에서 실제로 쓰인 마커 계위 중 **표/본문을 가장 잘 나누는 레벨**을 고른다:
    한글 가·나(level2)가 있으면 그것을, 없으면 등장한 마커레벨 중 2번째 상위를 카드 경계로.
    """
    blocks = iter_blocks(html)
    levels_present = sorted({marker_level(b.text) for b in blocks if b.kind == "text"
                             and marker_level(b.text) is not None})
    if not levels_present:
        # 마커 전무 → 전체를 한 카드(후속 표룰이 처리)
        return [Card(marker="", title="", level=0, blocks=blocks)]
    # 카드 경계 레벨: 한글 가·나(2) 있으면 2, 아니면 등장 레벨 중 상위 2번째(최상위는 대개 章)
    card_level = 2 if 2 in levels_present else (levels_present[1] if len(levels_present) > 1 else levels_present[0])

    cards: list[Card] = []
    cur: Card | None = None
    preamble: list[Block] = []
    for b in blocks:
        lvl = marker_level(b.text) if b.kind == "text" else None
        if lvl is not None and lvl <= card_level and b.kind == "text":
            # 새 카드 시작(카드경계 레벨 이상 상위 마커)
            cur = Card(marker=(b.text.split()[0] if b.text.split() else ""),
                       title=strip_marker(b.text), level=lvl)
            cards.append(cur)
        elif cur is None:
            preamble.append(b)             # 첫 카드 전 서두
        else:
            cur.blocks.append(b)
    if preamble and cards:
        cards[0].blocks = preamble + cards[0].blocks
    elif preamble and not cards:
        cards = [Card(marker="", title="", level=0, blocks=preamble)]
    return cards
