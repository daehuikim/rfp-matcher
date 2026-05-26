from __future__ import annotations

import re

# 요구사항 본문이 있을 가능성이 높은 대목차 키워드
REQUIREMENT_SECTION_KEYWORDS: tuple[re.Pattern[str], ...] = (
    re.compile(r"프로젝트\s*범위", re.I),
    re.compile(r"제\s*안\s*범\s*위", re.I),
    re.compile(r"구축\s*범위", re.I),
    re.compile(r"요구\s*사항", re.I),
    re.compile(r"요\s*건", re.I),
    re.compile(r"요\s*구", re.I),
    re.compile(r"제\s*안\s*요\s*구\s*사\s*항", re.I),
    re.compile(r"아키텍처\s*요구", re.I),
    re.compile(r"기능\s*요구", re.I),
    re.compile(r"비\s*기능", re.I),
    re.compile(r"제\s*안\s*요\s*청\s*내\s*용", re.I),
    re.compile(r"상세\s*요구", re.I),
    re.compile(r"업무\s*요건", re.I),
    re.compile(r"시스템\s*요구", re.I),
    re.compile(r"업무\s*및\s*기술\s*요건", re.I),
    re.compile(r"기술\s*요건", re.I),
)

# 요구사항 구역 종료 — 이 헤더 이후 표는 조견표 후보에서 제외
NON_REQUIREMENT_SECTION_KEYWORDS: tuple[re.Pattern[str], ...] = (
    re.compile(r"제\s*출\s*서\s*류", re.I),
    re.compile(r"제\s*출\s*방\s*법", re.I),
    re.compile(r"제\s*안\s*일\s*정", re.I),
    re.compile(r"입\s*찰", re.I),
    re.compile(r"계\s*약", re.I),
    re.compile(r"서\s*식", re.I),
    re.compile(r"별\s*첨", re.I),
    re.compile(r"일\s*반\s*현\s*황", re.I),
    re.compile(r"재\s*무", re.I),
    re.compile(r"인\s*력", re.I),
    re.compile(r"평\s*가\s*방\s*법", re.I),
    re.compile(r"목\s*차", re.I),
    re.compile(r"표\s*준\s*환\s*경", re.I),
)

# 대목차: "3. 프로젝트 범위", "1.3 제안 범위", "2.2 제안 요구사항 상세", "Ⅱ. 제안요청 내용"
MAJOR_SECTION_RE = re.compile(
    r"^\s*(?:"
    r"(?P<num>\d+(?:\.\d+)*)(?:\.\s*|\s+)(?P<num_title>.+?)"
    r"|(?P<roman>[Ⅰ-Ⅹ]+)\.\s*(?P<roman_title>.+?)"
    r")\s*$"
)

# 중목차: "가. 지식 기반 …"
SUBSECTION_RE = re.compile(r"^\s*(?P<letter>[가-힣])\.\s*(?P<sub_title>.+?)\s*$")

# 하위 항목: "1) Agent …", "(2) 상세 요구사항 내용"
SUBITEM_RE = re.compile(r"^\s*(?P<sub>\d+)\)\s*(?P<sub_body>.+)?\s*$")

# 본문 조견표 시작 앵커 — 「1.4.3 상세 요구사항」 개요보다 이 지점부터만 구역 오픈
DETAIL_REQUIREMENT_CONTENT_ANCHOR = re.compile(
    r"\(\s*(?P<num>\d+)\s*\)\s*상세\s*요구\s*사항",
    re.I,
)
DETAIL_REQUIREMENT_CONTENT_TITLE = re.compile(r"상세\s*요구\s*사항\s*내용", re.I)

# PDF 추출 노이즈 — 단독으로는 무시하고 다음 줄과 붙임
NOISE_ONLY_RE = re.compile(r"^(?:□|⦁|○|●|·|ㄱ|ㄴ|ㄷ|[-–—])$")

# 본문 블록으로 볼 HTML 태그 (문서 순서 유지)
BLOCK_TAGS: tuple[str, ...] = ("h1", "h2", "h3", "h4", "p", "li")

MIN_ATOM_TEXT_LEN = 12
MIN_SECTION_ATOMS = 3
MIN_SECTION_BLOCK_SPAN = 20


def format_section_heading(match: re.Match[str]) -> str:
    if match.group("roman_title"):
        return f"{match.group('roman')}. {match.group('roman_title').strip()}"
    num = match.group("num")
    title = match.group("num_title").strip()
    if num and "." in num:
        return f"{num} {title}"
    return f"{num}. {title}"
