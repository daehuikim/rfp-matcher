"""
raw HTML 의 `< 관련 화면(안) >` 플레이스홀더 ↔ opendataloader 추출 이미지 순서 매칭.

후처리 HTML 은 img 를 제거하므로, raw.html + *_images/ 폴더 기준으로
**요구사항 본문용** 플레이스홀더(`관련 화면(안)`, `자료 입력 관련 화면(안)`)만
문서 순서대로 슬롯화하고, 조견표 상세요건과 1:1 매칭한다.
"""
from __future__ import annotations

import re
from copy import copy
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from .extract import Req
from .text import norm

# 요구사항 셀용 — '구축 예정 … 화면(안)' 등 비요구 플레이스홀더 제외
_SCREEN_PH = re.compile(
    r"(?:&lt;|<)\s*(?:자료\s*입력\s*)?관련\s*화면\s*\(\s*안\s*\)\s*(?:&gt;|>)",
    re.I,
)
_DASH_ONLY = re.compile(r"^[–\-—](?:\s*[–\-—])+\s*$")
_DASH_INLINE = re.compile(r"[–\-—](?:\s*[–\-—])+")
_NARRATIVE_FOOTNOTE = re.compile(
    r"구축\s*예정|활용|하여야|예시로서|볼\s*수\s*있|제안사|업로드\s*예정|업체$"
)
_ANCHOR_TAGS = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th"})
_MIN_IMG_PX = 48  # 별표(※) 등 장식용 소형 img 제외
_SCREEN_FOOTNOTE = re.compile(r"화면\s*\*(?![*\d])")


def has_screen_placeholder(text: str) -> bool:
    return bool(_SCREEN_PH.search(text or ""))


def strip_screen_placeholder(text: str) -> str:
    return norm(_SCREEN_PH.sub("", text or ""))


def has_dash_placeholder(text: str) -> bool:
    """PDF 변환 후 남는 '– –' 등 대시 전용 줄/접미 — 스크린샷 breadcrumb 자리."""
    t = norm(text or "")
    if not t:
        return False
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    if lines and _DASH_ONLY.match(lines[-1]):
        return True
    return bool(re.search(r"[–\-—](?:\s*[–\-—])+\s*$", t))


def strip_dash_placeholder(text: str) -> str:
    t = norm(text or "")
    t = re.sub(r"\s*[–\-—](?:\s*[–\-—])+\s*$", "", t)
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    while lines and _DASH_ONLY.match(lines[-1]):
        lines.pop()
    return norm("\n".join(lines))


def _resolve_img(html_dir: Path, src: str) -> Path | None:
    if not src:
        return None
    p = (html_dir / src).resolve()
    return p if p.is_file() else None


def _is_content_image(path: Path) -> bool:
    """본문 스크린샷만 — 별표·bullet 장식 img 제외."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
        # breadcrumb strip (45×28 등) — 화면 경로 스크린샷
        if w >= 40 and h >= 20:
            return True
        return w >= _MIN_IMG_PX and h >= _MIN_IMG_PX
    except Exception:
        return True


def _footnote_from_siblings(start: Tag | None) -> str:
    """img 직후 figcaption/p 의 *·※ 주석 텍스트."""
    if start is None:
        return ""
    for sib in start.next_siblings:
        if isinstance(sib, NavigableString):
            t = norm(str(sib))
            if t.startswith(("*", "※")):
                return t
            continue
        if not isinstance(sib, Tag):
            continue
        if sib.name == "img":
            break
        if sib.name in ("p", "figcaption", "h6", "span"):
            t = norm(sib.get_text(" ", strip=True))
            if t.startswith(("*", "※")) or "변동" in t or "개발" in t and "화면" in t:
                return t
        if sib.name in _ANCHOR_TAGS and has_screen_placeholder(sib.get_text(" ", strip=True)):
            break
    return ""


def _collect_following_images(anchor: Tag, html_dir: Path, *, max_imgs: int = 8) -> tuple[list[Path], str]:
    """플레이스홀더 직후 **같은 블록(li/td) 형제** img + 주석."""
    imgs: list[Path] = []
    seen: set[str] = set()
    footnote = ""

    def add(tag: Tag) -> None:
        src = tag.get("src") or ""
        if not src or src in seen:
            return
        p = _resolve_img(html_dir, src)
        if p and _is_content_image(p):
            seen.add(src)
            imgs.append(p)

    def walk_siblings(start: Tag | None) -> None:
        nonlocal footnote
        if start is None:
            return
        last_img: Tag | None = None
        for sib in start.next_siblings:
            if isinstance(sib, NavigableString):
                continue
            if not isinstance(sib, Tag):
                continue
            if sib.name == "img":
                add(sib)
                last_img = sib
            elif sib.name == "table":
                for img in sib.find_all("img", limit=max_imgs):
                    add(img)
                    last_img = img
                if not footnote and last_img:
                    footnote = _footnote_from_siblings(last_img)
                return
            elif sib.name in _ANCHOR_TAGS:
                if has_screen_placeholder(sib.get_text(" ", strip=True)):
                    return
                t = norm(sib.get_text(" ", strip=True))
                if t.startswith(("*", "※")):
                    footnote = t
            if len(imgs) >= max_imgs:
                break
        if not footnote and last_img:
            footnote = _footnote_from_siblings(last_img)

    walk_siblings(anchor)
    if not imgs:
        node: Tag | None = anchor
        for _ in range(5):
            if node is None or node.parent is None:
                break
            node = node.parent
            walk_siblings(node)
            if imgs:
                break
    return imgs, footnote


def extract_screen_image_slots(raw_html_path: Path) -> list[tuple[list[Path], str]]:
    """raw HTML 문서 순서 — 요구사항용 플레이스홀더마다 (이미지, 주석)."""
    raw_html_path = raw_html_path.resolve()
    html_dir = raw_html_path.parent
    soup = BeautifulSoup(
        raw_html_path.read_text(encoding="utf-8", errors="replace"), "lxml"
    )
    body = soup.body or soup
    slots: list[tuple[list[Path], str]] = []
    seen_anchors: set[int] = set()

    for el in body.find_all(_ANCHOR_TAGS):
        if id(el) in seen_anchors:
            continue
        text = el.get_text(" ", strip=True)
        if not has_screen_placeholder(text):
            continue
        if any(
            has_screen_placeholder(child.get_text(" ", strip=True))
            for child in el.find_all(_ANCHOR_TAGS)
        ):
            continue
        seen_anchors.add(id(el))
        slots.append(_collect_following_images(el, html_dir))
    return slots


def _format_screen_path(text: str) -> str:
    """'시장감시-조사착수 전...' → gold 스타일 '시장감시 – 조사착수 전 ...'."""
    t = norm(text).lstrip("*").strip()
    t = re.sub(r"\s*-\s*", " – ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return f"* {t}" if t else ""


def _split_numbered_footnote(text: str) -> list[str]:
    raw = norm(text).lstrip("*").strip()
    if not raw:
        return []
    parts = re.split(r"[①②③④⑤]", raw)
    return [p.strip(" ,") for p in parts if p.strip(" ,")]


def _is_breadcrumb_footnote(text: str) -> bool:
    """`*` 주석이 화면 경로(breadcrumb)인지 — 도메인 키워드 없이 구조로 판별."""
    t = norm(text).lstrip("*").strip()
    if not t or len(t) < 6 or len(t) > 200:
        return False
    if _NARRATIVE_FOOTNOTE.search(t):
        return False
    if re.search(r"[①②③④⑤]", t):
        return True
    if " – " in t:
        return True
    if len(re.findall(r"[-–]", t)) >= 2:
        return True
    return False


def _collect_breadcrumb_segments(raw_html_path: Path) -> list[str]:
    """문서 순서 — 구조적으로 화면 경로로 보이는 `*` 주석 세그먼트."""
    soup = BeautifulSoup(
        raw_html_path.read_text(encoding="utf-8", errors="replace"), "lxml"
    )
    body = soup.body or soup
    segments: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        formatted = _format_screen_path(raw)
        if formatted and formatted not in seen:
            seen.add(formatted)
            segments.append(formatted)

    for el in body.find_all(["p", "figcaption", "li", "h6"]):
        t = norm(el.get_text(" ", strip=True))
        if not t.startswith("*") or not _is_breadcrumb_footnote(t):
            continue
        if re.search(r"[①②③④⑤]", t):
            for part in _split_numbered_footnote(t):
                _add(part)
        else:
            _add(t)
    return segments


def _footnotes_in_cell(cell: Tag) -> list[str]:
    out: list[str] = []
    for el in cell.find_all(["p", "span", "li"]):
        t = norm(el.get_text(" ", strip=True))
        if t.startswith("*") and _is_breadcrumb_footnote(t):
            if re.search(r"[①②③④⑤]", t):
                out.extend(_format_screen_path(p) for p in _split_numbered_footnote(t))
            else:
                out.append(_format_screen_path(t))
    return out


def _resolve_slot_footnote(cell: Tag, last_img: Tag | None) -> str:
    """슬롯 셀 — img 직후·셀 내 `*` 주석 우선."""
    if last_img:
        fn = _footnote_from_siblings(last_img)
        if fn and _is_breadcrumb_footnote(fn):
            return _format_screen_path(fn)
    for p in _footnotes_in_cell(cell):
        return p
    return ""


_GENERIC_PATH_TOKENS = frozenset(
    {"가상자산", "자료", "거래소", "정보", "시스템", "화면", "요청", "관리", "제공", "개발"}
)


def _token_weight(token: str) -> int:
    return 1 if token in _GENERIC_PATH_TOKENS else 3


def _is_standalone_token(token: str, detail: str) -> bool:
    """사건번호 속 '사건' 등 부분문자열 오매칭 방지."""
    if not token or token not in detail:
        return False

    def _boundary(ch: str) -> bool:
        return not ch or not ("\uac00" <= ch <= "\ud7a3" or ch.isalnum())

    for m in re.finditer(re.escape(token), detail):
        start, end = m.span()
        before = detail[start - 1] if start else " "
        after = detail[end] if end < len(detail) else " "
        if _boundary(before) and _boundary(after):
            return True
    return False


def _path_match_score(detail: str, path: str) -> int:
    """상세요건 ↔ 화면 경로 주석 유사도 (범용 토큰 가중치↓)."""
    score = 0
    if _SCREEN_FOOTNOTE.search(detail) and "화면" in path:
        score += 8
    if re.search(r"품의\s*\*", detail) and ("품의" in path or "매매" in path):
        score += 10
    tokens = [k for k in re.split(r"[\s–\-·'']+", path) if len(k) >= 2]
    for k in tokens:
        w = _token_weight(k)
        if _is_standalone_token(k, detail):
            score += len(k) * w
            continue
        matched = False
        for sub in re.findall(r"[가-힣]{2,}", k):
            if _is_standalone_token(sub, detail):
                score += len(sub) * _token_weight(sub)
                matched = True
        if matched:
            continue
        for frag_len in range(min(6, len(k)), 1, -1):
            for i in range(len(k) - frag_len + 1):
                frag = k[i : i + frag_len]
                if _is_standalone_token(frag, detail):
                    score += frag_len * _token_weight(frag)
                    break
            else:
                continue
            break
    return score


def _match_screen_footnote(detail: str, candidates: list[str]) -> str:
    """상세요건 ↔ breadcrumb 주석 — 토큰 유사도(구조 수집 후 매칭)."""
    if not candidates:
        return ""
    d = norm(detail)
    best = ""
    best_score = 0
    for c in candidates:
        path = c.lstrip("*").strip()
        score = _path_match_score(d, path)
        if score > best_score:
            best_score = score
            best = c
    return best if best_score >= 6 else ""


def extract_inline_figure_slots(raw_html_path: Path) -> list[tuple[list[Path], str]]:
    """form 셀 내 본문* 직후 스크린샷 — 후처리 시 '– –' 만 남는 케이스."""
    raw_html_path = raw_html_path.resolve()
    html_dir = raw_html_path.parent
    soup = BeautifulSoup(
        raw_html_path.read_text(encoding="utf-8", errors="replace"), "lxml"
    )
    body = soup.body or soup
    slots: list[tuple[list[Path], str]] = []

    for cell in body.find_all(["td", "th"]):
        text = cell.get_text(" ", strip=True)
        if "*" not in text or not _DASH_INLINE.search(text):
            continue
        imgs: list[Path] = []
        last_img: Tag | None = None
        seen: set[str] = set()
        for img in cell.find_all("img"):
            src = img.get("src") or ""
            if not src or src in seen:
                continue
            p = _resolve_img(html_dir, src)
            if p and _is_content_image(p):
                seen.add(src)
                imgs.append(p)
                last_img = img
        if imgs:
            slots.append((imgs, _resolve_slot_footnote(cell, last_img)))
    return slots


def attach_screen_images(reqs: list[Req], raw_html_path: Path | None) -> tuple[list[Req], int]:
    """상세요건 플레이스홀더 ↔ raw 슬롯 순서 매칭."""
    if raw_html_path is None or not raw_html_path.is_file():
        return reqs, 0

    slots = extract_screen_image_slots(raw_html_path)
    slot_i = 0
    out: list[Req] = []
    matched = 0

    for r in reqs:
        if not has_screen_placeholder(r.detail):
            out.append(r)
            continue

        imgs, footnote = slots[slot_i] if slot_i < len(slots) else ([], "")
        slot_i += 1
        clean = strip_screen_placeholder(r.detail)

        if not imgs:
            nr = copy(r)
            nr.detail = clean or r.detail
            out.append(nr)
            continue

        matched += 1
        if clean:
            base = copy(r)
            base.detail = clean
            base.detail_images = []
            out.append(base)

        cap_parts = ["[관련 화면(안)]"]
        if footnote:
            cap_parts.append(footnote)
        cap = copy(r)
        cap.detail = "\n".join(cap_parts)
        cap.detail_images = [str(p) for p in imgs]
        out.append(cap)

    return out, matched


def attach_figure_images(reqs: list[Req], raw_html_path: Path | None) -> tuple[list[Req], int]:
    """상세요건 '– –' 대시 자리 ↔ raw form 셀 스크린샷 순서 매칭."""
    if raw_html_path is None or not raw_html_path.is_file():
        return reqs, 0

    slots = extract_inline_figure_slots(raw_html_path)
    breadcrumb_segments = _collect_breadcrumb_segments(raw_html_path)
    slot_i = 0
    out: list[Req] = []
    matched = 0

    for r in reqs:
        if not has_dash_placeholder(r.detail):
            out.append(r)
            continue

        imgs, footnote = slots[slot_i] if slot_i < len(slots) else ([], "")
        slot_i += 1
        clean = strip_dash_placeholder(r.detail)
        screen_path = footnote or _match_screen_footnote(clean, breadcrumb_segments)

        if screen_path and clean:
            base = copy(r)
            base.detail = f"{clean}\n{screen_path}".strip()
            base.detail_images = []
            out.append(base)
            matched += 1
            continue

        if not imgs:
            nr = copy(r)
            nr.detail = clean or r.detail
            out.append(nr)
            continue

        matched += 1
        if clean:
            base = copy(r)
            base.detail = clean
            base.detail_images = []
            out.append(base)

        cap_parts = ["[관련 화면(안)]"]
        if screen_path:
            cap_parts.append(screen_path)
        cap = copy(r)
        cap.detail = "\n".join(cap_parts)
        cap.detail_images = [str(p) for p in imgs]
        out.append(cap)

    return out, matched
