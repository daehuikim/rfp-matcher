"""HTML → 문서순 블록 → **가/나 등 마커 기반 카드 유닛** 분할 (전처리 포함).

비정형 입력(무엇이든)이 HTML 로 바뀐 뒤, hwp5html 처럼 heading/list 태그가 없어도
**텍스트 선두 마커**(로마자/숫자/한글 가·나/ㄱ·ㄴ/1)·2)/❍·-)로 계위를 읽어 카드로 자른다.
카드 = '가. xxx요구사항' 같은 유닛 + 그 아래 본문/표. 하드코딩·키워드 룰 없음(마커 형태만).
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag

# 셀 안 이미지/중첩표 삽입 마커 — 다운스트림(table_extract/card_extract)에는 짧은 텍스트로만
# 보여 뚱뚱행/분해 로직을 건드리지 않고, adapter.py 에서 Req.detail_images 로 뽑아 실제
# 엑셀 이미지로 앉힌다("표를 인식→HTML렌더→이미지화→셀에 삽입" 요청 반영).
IMG_MARKER_RE = re.compile(r"\[(?:표이미지|첨부이미지):([^\]]+)\]")

# 문서 고유번호 형태(SFR-001 또는 SFR-LDA-001 같은 3단 체계) — 중첩표가 장비스펙표 같은
# '표안의 표'(평탄화/이미지화 대상)인지, 아니면 그 자체로 독립된 요구사항 카드(라벨-값
# 세로카드)인지 구분하는 데 쓴다. card_extract._DOC_ID 와 동일 패턴(모듈 결합 피하려 중복).
_CARD_ID_HINT = re.compile(r"\b[A-Z]{2,4}(?:[-–][A-Z]{2,6})?[-–]\d{2,4}\b")


def _looks_like_requirement_card(tbl: Tag) -> bool:
    """중첩표가 요구사항 카드(문서 고유번호 보유, 대체로 소규모)인지 — 맞으면 이미지화/
    텍스트평탄화 대신 자기 자신을 별도 최상위 블록으로 뽑아 구조(code_hint 등)를 보존한다.
    실측(양형): 진짜 SFR-XXX 카드 49개가 '컨테이너 표' 안에 중첩표로 들어있었는데, 전부
    표안의표로 취급돼 이미지 렌더링되면서 고유번호·구조가 통째로 유실됐다."""
    rows = tbl.find_all("tr")
    if len(rows) > 30:          # 장비스펙표 등 대규모 데이터 그리드는 카드가 아님
        return False
    return bool(_CARD_ID_HINT.search(tbl.get_text(" ", strip=True)))


def _contains_requirement_card(tbl: Tag) -> bool:
    """자신은 카드가 아니어도(여러 카드를 감싼 컨테이너/래퍼 표 — 합치면 30행 넘어 그 자체는
    _looks_like_requirement_card 가 False), 안에 진짜 카드가 하나라도 있으면 True.
    _cell() 이 그 컨테이너를 '표안의표'로 보고 통째로 텍스트 평탄화하면, 이미 별도
    최상위 블록으로 뽑힌 안쪽 카드의 내용이 flat 텍스트로 중복 노출된다(실측: 양형에서
    코드 정상 추출된 카드와 똑같은 내용이 코드 없는 flat 블롭으로 한 번 더 나옴) — 컨테이너도
    같이 건너뛰어야 한다."""
    if _looks_like_requirement_card(tbl):
        return True
    return any(_looks_like_requirement_card(t) for t in tbl.find_all("table"))

# ── 마커 계위 사다리(형태만; 문서 도메인 무관) ──────────────────────────────
# level 작을수록 상위. 같은 문서 안에서 상대 계위로 카드 경계를 잡는다.
# 유니코드 로마자(Ⅰ-Ⅻ, hwp5html 이 표 셀에 쓰는 형태) + ASCII 로마자 모두 지원. 점 선택(표행은 점 없음).
# ASCII 로마자는 반드시 점을 요구(I. II.)해야 'MDM/VM/VDI/ICM' 같은 영문약어 오탐 방지.
# 유니코드 로마자(Ⅰ-Ⅺ, hwp5html 표셀)는 점 선택.
_ROMAN_HEAD = r"(?:[IVXLCDM]{1,4}\.|[Ⅰ-Ⅻ]\.?)"
# 불릿 문자류 — ⦁(U+2981)·•(U+2022)·▣◈◇▶▷‣⁃ 등 사설/도형 불릿 포함(형태 기반, 도메인 무관).
_BULLET = r"[❍○●◦▪▫◆◇■□▣◈▶▷▸‣⁃∙·•⦁*※\-–—]"
_MARKERS: list[tuple[int, "re.Pattern[str]"]] = [
    (0, re.compile(rf"^\s*(?:제\s*\d+\s*[부편]|{_ROMAN_HEAD})\s")),          # 제1부 / I. / Ⅰ
    (1, re.compile(r"^\s*(?:제\s*\d+\s*장|\d+\.)(?!\d)\s")),                # 제1장 / 1.
    (2, re.compile(r"^\s*\d+\.\d+\.?(?!\d)\s")),                           # 1.1 / 2.4. (후행점 허용)
    (2, re.compile(r"^\s*[가-힣]\.\s")),                                    # 가. 나.
    (3, re.compile(r"^\s*\d+\.\d+\.\d+\.?(?!\d)\s")),                      # 1.1.1
    (3, re.compile(r"^\s*[ㄱ-ㅎ]\.\s")),                                    # ㄱ. ㄴ.
    (4, re.compile(r"^\s*(?:\d+\s*-\s*\d+\)|\d+\)|[가-힣]\)|\([0-9가-힣]+\)|[①-⑳])\s?")),  # 1) 1-1) 1 - 1)(변환기 공백삽입) 가) (1) ①
    (5, re.compile(rf"^\s*{_BULLET}\s?")),                                 # 불릿(말단) — 후행공백 선택(■기능요구사항 밀착)
]
# 대안 순서 주의: '\d+\)' 가 단독숫자형 '\d+(?:\.\d+)*\.?' **앞**이어야 한다 —
# 뒤에 두면 '1) 지식저장소…'에서 숫자만 떼고 ')'가 남는다(신한은행 실측 ') 지식저장소').
_MARK_ANY = re.compile(rf"^\s*(?:제\s*\d+\s*[부편장]|{_ROMAN_HEAD}|"
                       rf"\d+\s*-\s*\d+\)|\d+\)|\d+(?:\.\d+)*\.?|[가-힣][.)]|[ㄱ-ㅎ]\.|\([0-9가-힣]+\)|[①-⑳]|{_BULLET})\s?")


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
    htag: int = 0        # HTML 헤딩 태그 레벨(h1=1..h6=6, 0=아님) — DOCX/HWP 순수제목 헤딩용
    li_depth: int = 0    # <li> 중첩 깊이(ul/ol 조상 수, 0=리스트 아님) — 리스트 계위 복원용
    table_index: int | None = None  # 변환 HTML 안 <table> 전체(중첩 포함) 문서순 인덱스 —
    # 프론트 원문뷰어가 querySelectorAll("table")[idx] 로 스크롤하는 것과 동일 기준(table
    # 블록에만 부여, text 블록은 None). PDF/HWP/DOCX 공통.
    page: int | None = None  # 원본 PDF 페이지(1-based) — opendataloader JSON에서 역주입.
    # HWP/DOCX 등 페이지 개념이 없거나 아직 못 채운 포맷은 None(뷰어는 table_index로 대체).


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


class CellImageSink:
    """문서 1건 변환 동안 재사용하는 렌더러 — 중첩표를 PNG로, 셀 이미지를 파일로 저장.

    브라우저를 표마다 새로 띄우면 문서당 수십 개에서 느려지므로 iter_blocks 1회 호출동안
    하나만 띄우고 끝에서 닫는다(close). 렌더/저장 실패는 예외를 삼키고 None을 돌려줘
    호출부가 기존 텍스트 폴백으로 되돌아가게 한다 — 이미지화 실패가 recall 을 깎지 않음.
    """

    def __init__(self, img_dir: Path, base_dir: Path | None = None):
        self.img_dir = Path(img_dir)
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self._page = None
        self._browser = None
        self._pw = None
        self._seq = 0

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._page = self._browser.new_page()
        return self._page

    def render_table(self, table_el: Tag) -> str | None:
        """셀 안 중첩표 전체를 PNG로 렌더링 — 파일명(문서 내 유일) 반환, 실패 시 None."""
        try:
            page = self._ensure_page()
            self.img_dir.mkdir(parents=True, exist_ok=True)
            self._seq += 1
            fname = f"tbl_{self._seq:04d}.png"
            style = ("<style>table{border-collapse:collapse;font-family:sans-serif;"
                      "font-size:13px;background:#fff}td,th{border:1px solid #999;"
                      "padding:4px 7px;vertical-align:top}</style>")
            page.set_content(f"<html><body style='margin:0'>{style}{table_el}</body></html>")
            page.locator("table").first.screenshot(path=str(self.img_dir / fname))
            return fname
        except Exception:
            return None

    def save_img(self, img_el: Tag) -> str | None:
        """셀 안 <img> 원본을 저장 — data URI 디코드 또는 bindata 상대경로 복사."""
        src = (img_el.get("src") or "").strip()
        if not src:
            return None
        try:
            self.img_dir.mkdir(parents=True, exist_ok=True)
            if src.startswith("data:image/"):
                header, _, b64 = src.partition(",")
                if not b64:
                    return None
                ext = re.sub(r"[^a-z0-9]", "", header.split("/")[1].split(";")[0])[:4] or "png"
                data = base64.b64decode(b64)
                self._seq += 1
                fname = f"img_{self._seq:04d}.{ext}"
                (self.img_dir / fname).write_bytes(data)
                return fname
            if self.base_dir is not None:
                srcp = (self.base_dir / src).resolve()
                if srcp.is_file():
                    ext = srcp.suffix.lstrip(".") or "png"
                    self._seq += 1
                    fname = f"img_{self._seq:04d}.{ext}"
                    (self.img_dir / fname).write_bytes(srcp.read_bytes())
                    return fname
        except Exception:
            return None
        return None

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass


def _cell(td: Tag, owner: Tag | None = None, sink: CellImageSink | None = None) -> str:
    """셀 텍스트 — **줄 경계 보존**. 셀 안 <p>/<li> 는 각각 한 줄, 중첩표는 행당 한 줄
    (셀 " | " join), <img> 는 [그림] 플레이스홀더 한 줄.

    기존 get_text(" ")는 셀 내부 모든 줄을 공백으로 이어붙여 불릿 여러 개가 대시로 연결된
    2000자짜리 '뚱뚱한 행'이 됐다(기아 실측 최대 2,286자). 줄 경계가 살아야 표추출의
    split_items 가 의미단위(불릿 1개=행 1개)로 분해할 수 있다. 구조가 없는 평문 셀은
    기존과 동일하게 한 줄로 나온다.
    """
    owner = owner if owner is not None else td.find_parent("table")

    def _one(el: Tag) -> str:
        return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()

    def _flat(el: Tag) -> str:
        """평문 폴백 — 텍스트 노드 **안의 실제 개행**만 줄 경계로 보존(스캔 셀 랩핑).
        태그 경계는 공백 join(개행으로 하면 <b>등 인라인 태그마다 가짜 줄이 생김)."""
        raw = el.get_text(" ")
        if "\n" not in raw:
            return re.sub(r"\s+", " ", raw).strip()
        return "\n".join(re.sub(r"[ \t]+", " ", ln).strip()
                         for ln in raw.split("\n") if ln.strip())

    lines: list[str] = []
    omitted = False   # 요구사항 카드라 의도적으로 뺀 중첩표가 있었는지 — 있었는데 lines 가
    # 그대로 비면 아래 "구조 없는 평문 셀" 폴백(_flat)이 owner 스코프를 무시하고 td 의
    # get_text() 로 그 카드 텍스트를 통째로 다시 주워담아 중복 노출한다(실측 버그) —
    # 그 폴백을 막기 위한 플래그.
    for el in td.find_all(["p", "li", "table", "img"]):   # 문서순
        if el.name == "table":
            if el.find_parent("table") is not owner:
                continue                     # 표안표안표 — 직속 중첩표 처리에 포함됨
            if _contains_requirement_card(el):
                omitted = True
                continue   # 요구사항 카드(들)는 별도 최상위 블록으로 이미 뽑힘(중복 방지) —
                # el 자신이 카드거나, 여러 카드를 감싼 컨테이너라도 안에 카드가 있으면 스킵.
            fname = sink.render_table(el) if sink is not None else None
            if fname:
                lines.append(f"[표이미지:{fname}]")   # 텍스트 평탄화 대신 표 자체를 이미지로
                continue
            for tr in el.find_all("tr"):
                if tr.find_parent("table") is not el:
                    continue
                cells = [_one(c) for c in tr.find_all(["td", "th"], recursive=False)]
                row = " | ".join(c for c in cells if c)
                if row:
                    lines.append(row)
            continue
        if el.find_parent("table") is not owner:
            continue                         # 중첩표 내부 p/li/img — 위 표 라인으로 처리됨
        if el.name == "img":
            fname = sink.save_img(el) if sink is not None else None
            lines.append(f"[표이미지:{fname}]" if fname else "[그림]")
        elif el.name == "p":
            if el.find_parent("li") is not None:
                continue                     # li 안 p 는 li 줄로 처리(중복 방지)
            t = _one(el)
            if t:
                lines.append(t)
        else:                                # li — 직계 텍스트만(중첩 하위 li 는 자기 줄로)
            t = re.sub(r"\s+", " ", " ".join(
                s.strip() for s in el.find_all(string=True)
                if s.find_parent("li") is el and s.strip())).strip()
            if t:
                lines.append(t)
    if not lines:
        return "" if omitted else _flat(td)   # 카드 생략으로 빈 것과 진짜 평문 셀을 구분
    # p/li 밖 직계 텍스트(NavigableString) 유실 방지
    direct = " ".join(s.strip() for s in td.find_all(string=True, recursive=False) if s.strip())
    if direct:
        lines.insert(0, re.sub(r"\s+", " ", direct))
    return "\n".join(lines)


def _table_grid(tbl: Tag, sink: CellImageSink | None = None) -> list[list[str]]:
    """표 → 2차원 문자열 그리드. rowspan/colspan 을 펼쳐 열 정렬을 유지한다 — 안 그러면
    (펼치기 전) rowspan 있는 라벨 열 아래 행들이 한 칸씩 밀려, 세로카드 라벨-값 파싱이
    그 칸을 값이 아니라 라벨로 오인한다. 실측: 양형 HWP의 큰 병합 카드표에서 이 밀림 때문에
    문서고유번호(SFR-XXX)가 값이 아니라 라벨/이름 칸으로 새어나가 코드가 통째로 유실됐다."""
    rows: list[list[str]] = []
    pending: dict[int, list] = {}   # col -> [남은 rowspan, 값] — 세로로 이어지는 rowspan 값
    for tr in tbl.find_all("tr"):
        if tr.find_parent("table") is not tbl:
            continue
        row: list[str] = []
        tds = tr.find_all(["td", "th"], recursive=False)
        ti = 0
        col = 0
        while ti < len(tds) or col in pending:
            if col in pending:
                entry = pending[col]
                row.append(entry[1])
                if entry[0] <= 1:
                    del pending[col]
                else:
                    entry[0] -= 1
                col += 1
                continue
            td = tds[ti]; ti += 1
            val = _cell(td, tbl, sink)
            try:
                colspan = max(1, int(td.get("colspan", 1)))
            except (TypeError, ValueError):
                colspan = 1
            try:
                rowspan = max(1, int(td.get("rowspan", 1)))
            except (TypeError, ValueError):
                rowspan = 1
            for k in range(colspan):
                row.append(val)
                if rowspan > 1:
                    pending[col + k] = [rowspan - 1, val]
            col += colspan
        rows.append(row)
    return rows


def iter_blocks(html: str, img_dir: Path | str | None = None,
                base_dir: Path | str | None = None) -> list[Block]:
    """문서순 블록(text/table). 전처리: p/td 중복·빈 블록·연속중복 제거(hwp5html 잡음).

    img_dir 을 주면 셀 내부 중첩표/이미지를 텍스트 평탄화 대신 PNG 로 렌더링해 저장하고
    "[표이미지:파일명]" 마커만 남긴다(호출부가 Req.detail_images 로 뽑아 엑셀에 실제
    이미지로 삽입). img_dir 이 None 이면 기존 텍스트 평탄화 동작 그대로(하위호환).
    """
    soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    sink = CellImageSink(img_dir, base_dir) if img_dir is not None else None
    try:
        return _iter_blocks_body(body, sink)
    finally:
        if sink is not None:
            sink.close()


def _elem_page(el: Tag) -> int | None:
    """el 또는 조상에 실린 data-page 속성(원문뷰어 페이지점프용, convert.py 가 주입) 읽기."""
    cur = el
    while cur is not None and getattr(cur, "name", None) is not None:
        v = cur.get("data-page")
        if v:
            try:
                return int(v)
            except ValueError:
                pass
        cur = cur.parent
    return None


def _iter_blocks_body(body, sink: CellImageSink | None) -> list[Block]:
    # 원문뷰어가 querySelectorAll("table")[idx] 로 스크롤하는 것과 동일 기준의 문서순
    # 인덱스 — 중첩표 포함 전체 <table> 순서(top-level 만 세면 프론트 인덱스와 어긋남).
    all_tables = body.find_all("table")
    table_idx_of = {id(t): i for i, t in enumerate(all_tables)}

    out: list[Block] = []
    seen_recent: list[str] = []
    for el in body.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"]):
        if el.name != "table" and el.find_parent("table"):
            continue                       # 표 안 텍스트는 table 블록으로만
        if el.name == "table":
            is_nested = el.find_parent("table") is not None
            if is_nested and not _looks_like_requirement_card(el):
                continue    # 표안의표(장비스펙 등) — 바깥표 처리 중 이미지화/평탄화됨
            grid = _table_grid(el, sink)
            if any(c.strip() for r in grid for c in r):
                out.append(Block(kind="table", grid=grid,
                                  table_index=table_idx_of.get(id(el)),
                                  page=_elem_page(el)))
                seen_recent = []
            continue
        li_depth = 0
        if el.name == "li":
            # 직계 텍스트만(하위 중첩 리스트·중첩 표 제외) — 부모 li 가 자식 li/표 전체
            # 텍스트를 중복 방출하던 문제 해결(JB 실측: li 252개 중 부모/자식 이중 방출로
            # 중복행 다수). 자식 li·표는 자기 블록으로 따로 나오므로 내용 소실 없음.
            t = re.sub(r"\s+", " ", " ".join(
                s.strip() for s in el.find_all(string=True)
                if s.find_parent("li") is el and s.find_parent("table") is None
                and s.strip())).strip()
            li_depth = len(el.find_parents(["ul", "ol"]))
        else:
            t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if not t:
            continue
        if t in seen_recent:               # 직전 몇 블록과 동일 텍스트 → hwp5html 중복 제거
            continue
        htag = int(el.name[1]) if el.name in ("h1", "h2", "h3", "h4", "h5", "h6") else 0
        out.append(Block(kind="text", text=t, htag=htag, li_depth=li_depth, page=_elem_page(el)))
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
