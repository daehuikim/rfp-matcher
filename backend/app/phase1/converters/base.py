from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.domain.models import Document, HtmlDoc


class HtmlConverter(ABC):
    """
    비정형 문서 → HTML 변환기 추상.

    출력 HTML은 다음 두 가지를 보존해야 한다:
      1) 표(<table>/<tr>/<td>) 구조 — 병합셀은 rowspan/colspan으로
      2) 단락 순서 — <p>로 감싸 줄바꿈 보존
    """

    @abstractmethod
    async def convert(self, document: Document, out_dir: Path) -> HtmlDoc: ...


def count_html_features(html: str) -> tuple[int, int]:
    """단순 카운트 — 셀 개수, 단락 개수. evaluator·sanity check에서 사용."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    cells = len(soup.find_all(["td", "th"]))
    paragraphs = len(soup.find_all("p"))
    return cells, paragraphs
