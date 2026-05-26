from __future__ import annotations

import re
from dataclasses import dataclass

# ①~⑳ 원숫자
CIRCLED_DIGITS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
# ㈀~㈎ 한글 원괄호 (드물게 등장)
PAREN_HANGUL = "㈀㈁㈂㈃㈄㈅㈆㈇㈈㈉㈊㈋㈌㈍㈎"

# 조견표 '한 줄' 경계 — ①②③·(1)·1.·가. 등 상위 항목
_PRIMARY_MARKER_RE = re.compile(
    r"^\s*("
    rf"[{CIRCLED_DIGITS}{PAREN_HANGUL}]"  # ①, ㈀
    r"|\([0-9]+\)"  # (1)
    r"|[0-9]+[.)]"  # 1. or 1)
    r"|[가-힣]\."  # 가.
    r")\s*"
)

# ① 아래 하위 불릿 — 상위 마커가 있으면 분해하지 않고 본문에 포함
_SECONDARY_MARKER_RE = re.compile(
    r"^\s*("
    r"[•·●▪▫◦‣⁃○]"  # 불릿 기호
    r"|-\s"  # 하이픈 + 공백
    r")\s*"
)

_HANGUL = re.compile(r"[가-힣]")
_SENTENCE_END = re.compile(r"(?:다|요|음|함|임|됨|것)\.\s*$|[.!?。:;)\]]\s*$")


@dataclass
class Atom:
    marker: str | None
    text: str


def split_by_markers(cell_text: str) -> list[Atom]:
    """
    셀(또는 단락) 텍스트를 조견표 '한 줄' 단위로 분해.

    규칙:
      - ①②③·(1)·1.·가. → 새 항목 시작 (조견표 1행)
      - •·- 등 하위 불릿 → 직전 ① 항목 본문에 이어 붙임 (별도 행으로 쪼개지 않음)
      - 셀 전체에 상위 마커가 없을 때만 •·- 로 분해 (불릿만 있는 목록)
    """
    lines = [ln.strip() for ln in cell_text.splitlines() if ln.strip()]
    if not lines:
        return []

    has_primary = any(_PRIMARY_MARKER_RE.match(ln) for ln in lines)

    atoms: list[Atom] = []
    current: Atom | None = None
    for ln in lines:
        m_pri = _PRIMARY_MARKER_RE.match(ln)
        m_sec = _SECONDARY_MARKER_RE.match(ln)

        if m_pri:
            if current is not None:
                atoms.append(current)
            marker = m_pri.group(1).strip()
            body = ln[m_pri.end() :].strip()
            current = Atom(marker=marker, text=body)
        elif m_sec and not has_primary:
            if current is not None:
                atoms.append(current)
            marker = m_sec.group(1).strip()
            body = ln[m_sec.end() :].strip()
            current = Atom(marker=marker, text=body)
        elif current is None:
            current = Atom(marker=None, text=ln)
        else:
            sep = "\n" if current.text else ""
            current.text = f"{current.text}{sep}{ln}".strip()
    if current is not None:
        atoms.append(current)
    for atom in atoms:
        atom.text = normalize_line_breaks(atom.text)
    return atoms


def normalize_line_breaks(text: str) -> str:
    """
    PDF/HTML 추출 시 줄 중간에 끊긴 줄바꿈을 합친다.

    •·① 등 목록 경계, 문장 종결(다./요. 등) 뒤 줄바꿈은 유지한다.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return text.strip()

    merged: list[str] = []
    buf = lines[0]
    for nxt in lines[1:]:
        if _should_merge_lines(buf, nxt):
            buf = _join_merged_lines(buf, nxt)
        else:
            merged.append(buf)
            buf = nxt
    merged.append(buf)
    return "\n".join(merged)


def _join_merged_lines(prev: str, nxt: str) -> str:
    """단어 중간 줄바꿈(확+대를)은 붙이고, 문장 이어짐(제공해야+합니다)은 공백."""
    last = re.search(r"(\S+)$", prev)
    fragment = last.group(1) if last else ""
    if fragment and len(fragment) <= 3 and _HANGUL.search(fragment) and _HANGUL.match(nxt):
        return prev[: -len(fragment)] + fragment + nxt
    return f"{prev} {nxt}"


def _should_merge_lines(prev: str, nxt: str) -> bool:
    if _PRIMARY_MARKER_RE.match(nxt) or _SECONDARY_MARKER_RE.match(nxt):
        return False
    if _SENTENCE_END.search(prev):
        return False
    if prev.endswith((",", "，", ";", "；")):
        return False
    # 한글 PDF: '확' + '대를' 처럼 단어 중간 줄바꿈
    if _HANGUL.search(prev[-1:]) and _HANGUL.match(nxt):
        return True
    # 영문 단어 중간 줄바꿈
    if prev and prev[-1].isalnum() and nxt and nxt[0].islower():
        return True
    return False


def atom_title(text: str) -> str:
    """조견표 항목 제목 — ① 아래 첫 줄(한 문장)을 잘라내지 않고 사용."""
    normalized = normalize_line_breaks(text)
    first = normalized.split("\n", 1)[0].strip()
    m = _PRIMARY_MARKER_RE.match(first)
    if m:
        first = first[m.end() :].strip()
    return first
