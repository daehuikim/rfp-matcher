from __future__ import annotations

import html as html_lib
import re
from bs4 import BeautifulSoup

from rfpmatch.shared_html_builder import (
    promote_standalone_two_col_fragment_html,
    restore_table_fragments_in_html_raw,
)
from rfpmatch.shared_models import RfpCard, Section


def _full_html_or_text(text: str) -> str:
    value = text.strip()
    if value:
        return value
    return ""


def _restore_card_local_table_fragments(excerpt: str, title: str = "") -> str:
    html_excerpt = excerpt.strip()
    if not html_excerpt:
        return ""
    restored = restore_table_fragments_in_html_raw(html_excerpt)
    return restored


def _is_two_col_table(html: str) -> bool:
    if "<table" not in html.lower():
        return False
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return False
    rows = table.find_all("tr")
    if not rows:
        return False
    max_cols = 0
    for row in rows:
        cells = row.find_all(["td", "th"])
        max_cols = max(max_cols, len(cells))
    return max_cols == 2


def _last_cell_incomplete(html: str) -> bool:
    if "<table" not in html.lower():
        return False
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return False
    rows = table.find_all("tr")
    if not rows:
        return False
    last_row = rows[-1]
    cells = last_row.find_all(["td", "th"])
    if not cells:
        return False
    last_cell_text = cells[-1].get_text(" ", strip=True)
    if not last_cell_text:
        return False
    if last_cell_text[-1] not in {".", "?", "!", '"', "'", "}"}:
        return True
    return False


def _looks_like_table_continuation(current_text: str, prev_html: str, title: str = "") -> bool:
    if not _is_two_col_table(prev_html):
        return False
    lines = [line.strip() for line in current_text.split("\n") if line.strip()]
    if not lines:
        return False
    
    first_line = lines[0]
    
    # 0. title과 유사한 일반 본문 단락(예: 섹션 제목 그 자체)인 경우 제외
    if title:
        clean_first = re.sub(r"\s+", "", first_line)
        clean_title = re.sub(r"\s+", "", title)
        if clean_first == clean_title:
            return False

    # 1. 직전 표 마지막 셀이 미완성이며, 현재 첫 단어가 연결 어미로 시작
    if _last_cell_incomplete(prev_html):
        if first_line.startswith(("니다", "다.", "요.", "함.")):
            return True
            
    # 2. 직전 표 내부에 리스트 기호가 존재했고, 현재 카드의 첫 줄도 리스트 기호로 시작
    soup = BeautifulSoup(prev_html, "html.parser")
    prev_text = soup.get_text(" ", strip=True)
    has_list_in_prev = any(marker in prev_text for marker in {"①", "②", "•", "▪", "■", "◆", "▶", "◦", "○", "□"})
    if has_list_in_prev:
        # 다중 소수점 번호(예: 1.1., 1.1.1 등) 체계인 경우 리스트 기호에서 제외
        if re.match(r"^\d+\.\d+", first_line):
            return False
            
        if re.match(
            r"^(?:[•▪■◆▶◦○□◇·ㆍ−–—\-\*]+|(?:①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩)|\(?\d+[\)\.\-]|[가나다라마바사아자차카타파하][\.\)])\s*",
            first_line,
            flags=re.IGNORECASE
        ):
            return True
            
    return False


def _promote_to_right_only_two_col_table(text: str) -> str:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""
    content_html = "".join(f"<p>{html_lib.escape(line)}</p>" for line in lines)
    return f"<table><tbody><tr><td></td><td>{content_html}</td></tr></tbody></table>"


def build_rfp_cards(sections: list[Section]) -> list[RfpCard]:
    cards: list[RfpCard] = []
    stack: list[Section] = []

    for section in sections:
        while stack and stack[-1].level >= section.level:
            stack.pop()

        hierarchy = [*stack, section]
        level1_node = next((node for node in hierarchy if int(getattr(node, "level", 0) or 0) == 1), section)
        level2_node = next((node for node in hierarchy if int(getattr(node, "level", 0) or 0) == 2), None)
        level3_node = next((node for node in hierarchy if int(getattr(node, "level", 0) or 0) == 3), None)
        html_source = section.html if section.html.strip() else section.text
        excerpt = _restore_card_local_table_fragments(_full_html_or_text(html_source), title=section.title)

        # 직전 카드의 2단 표와 연결되는 꼬리 텍스트인 경우 강제 표 승격
        if cards and "<table" not in excerpt.lower():
            prev_card = cards[-1]
            if _looks_like_table_continuation(_full_html_or_text(html_source), prev_card.html_excerpt, title=section.title):
                excerpt = _promote_to_right_only_two_col_table(excerpt)

        cards.append(
            RfpCard(
                card_id=len(cards) + 1,
                card_no=str(len(cards) + 1),
                requirement=section.title,
                subject=section.title,
                part=level1_node.title if level1_node else "",
                section=level2_node.title if level2_node else "",
                category=level3_node.title if level3_node else "",
                html_excerpt=excerpt,
                page_idx=section.page_idx,
                anchor=section.anchor,
            )
        )
        stack.append(section)

    return cards
