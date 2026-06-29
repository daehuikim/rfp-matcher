"""조견표 db 모델 + 문서 계층(Part/Section/Subject/Sub-Subject) 분류.

스펙 계층 순서: Part(보통 I) > Section(1. 또는 가) > Subject(1.1 또는 가.) >
Sub-Subject(1.1.1) > 항목명 > 요구사항 > 상세요건.
기초정보 Section→Excel 탭. 계층정보 항목명/요구사항/상세요건 3단. 추가정보 Part/Subject/Sub-Subject/Page/참조사항.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Row:
    """조견표 db 한 행."""
    # 기초/추가 정보
    part: str = ""
    section: str = ""          # Excel 탭
    subject: str = ""
    sub_subject: str = ""
    page: int | None = None
    ref: str = ""              # 참조사항(수량/비고/번호 등)
    # 계층 정보(3단)
    item: str = ""             # 항목명
    requirement: str = ""      # 요구사항
    detail: str = ""           # 상세요건
    # 출처 추적
    origin: str = ""           # "table" | "body"
    table_id: int | None = None


# ── 계층(heading) 분류 — 스펙 번호 체계 ──────────────────────────────────────
_RE_PART = re.compile(r"^\s*(?:제\s*)?([IVXLCDM]+)\s*[.)]\s*\S")   # I. II. (로마자)
_RE_NUM = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")                # 1.  1.1  1.1.1
_RE_HANGUL = re.compile(r"^\s*([가-힣])\.\s+\S")                    # 가. 나.
_RE_PART_HANGUL = re.compile(r"^\s*제?\s*(\d+)\s*(?:부|장|편)\b")    # 제1부/1장


def heading_label(text: str) -> str:
    """heading 번호·기호 제거한 제목."""
    t = (text or "").strip()
    t = re.sub(r"^\s*(?:제\s*)?[IVXLCDM]+\s*[.)]\s*", "", t)
    t = re.sub(r"^\s*제?\s*\d+\s*(?:부|장|편)\s*[.)]?\s*", "", t)
    t = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s+", "", t)
    t = re.sub(r"^\s*[가-힣]\.\s+", "", t)
    return t.strip()


def _is_titleish(label: str) -> bool:
    """제목다움 — 번호/기호 제거 후 2자 이상 + 한글/영문 포함(숫자·기호 파편 배제)."""
    return len(label) >= 2 and bool(re.search(r"[가-힣A-Za-z]", label))


def heading_level(text: str) -> int | None:
    """heading 텍스트 → 계층 깊이(0=Part,1=Section,2=Subject,3=Sub-Subject) 또는 None.

    숫자 점 개수로 깊이를 잡고, 로마자=Part, 한글 가.=Section급. 단 번호 뒤 **실제 제목**이
    없으면(숫자·페이지 파편) heading 으로 보지 않는다.
    """
    t = (text or "").strip()
    if not t or len(t) > 80:
        return None
    label = heading_label(t)
    if not _is_titleish(label):
        return None
    if _RE_PART.match(t) or _RE_PART_HANGUL.match(t):
        return 0
    m = _RE_NUM.match(t)
    if m:
        depth = m.group(1).count(".")   # "1"→0, "1.1"→1, "1.1.1"→2
        return min(depth + 1, 3)        # Section=1, Subject=2, Sub-Subject=3
    if _RE_HANGUL.match(t):
        return 1                        # 가. 나. → Section 급
    return None


class HeadingStack:
    """Part/Section/Subject/Sub-Subject 컨텍스트 유지."""

    def __init__(self) -> None:
        self.slots = ["", "", "", ""]   # part, section, subject, sub_subject

    def push(self, level: int, label: str) -> None:
        self.slots[level] = label
        for j in range(level + 1, 4):    # 하위 슬롯 리셋
            self.slots[j] = ""

    @property
    def part(self) -> str: return self.slots[0]
    @property
    def section(self) -> str: return self.slots[1] or self.slots[0]
    @property
    def subject(self) -> str: return self.slots[2]
    @property
    def sub_subject(self) -> str: return self.slots[3]

    def has_subject(self) -> bool:
        return bool(self.slots[2] or self.slots[3])
