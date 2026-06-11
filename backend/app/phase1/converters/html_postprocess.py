"""
HTML 변환 결과 후처리 — 추출·탐지 성능을 위해 노이즈 제거·구조 단순화.

LibreOffice/mammoth 등이 남기는 font·style·meta·헤더/푸터 장식을 제거하고
표 구조(rowspan/colspan·data-page)와 본문 텍스트만 보존한다.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_KEEP_ATTRS = frozenset({"rowspan", "colspan", "data-page"})
_STRIP_TAGS = frozenset({"style", "script", "meta", "link", "title"})
_UNWRAP_TAGS = frozenset({"font", "span", "b", "i", "u", "strong", "em"})
_HEADER_FOOTER_TITLES = frozenset({"header", "footer"})


def compact_html(html: str) -> str:
  """인라인 스타일·폰트·장식 태그 제거, 표/단락 구조 유지."""
  soup = BeautifulSoup(html, "lxml")
  body = soup.body
  if body is None:
    return html

  for tag in soup.find_all(list(_STRIP_TAGS)):
    tag.decompose()

  for div in list(body.find_all("div")):
    title = (div.get("title") or "").strip().lower()
    if title in _HEADER_FOOTER_TITLES:
      div.decompose()

  for img in body.find_all("img"):
    img.decompose()

  for tag in list(body.find_all(_UNWRAP_TAGS)):
    if isinstance(tag, Tag):
      tag.unwrap()

  for tag in body.find_all(True):
    if not isinstance(tag, Tag):
      continue
    tag.attrs = {k: v for k, v in tag.attrs.items() if k in _KEEP_ATTRS}

  _collapse_empty_nodes(body)
  _normalize_whitespace(body)

  out = str(body)
  # body 래퍼만 반환 → 호출측에서 document shell 조립
  return out


def wrap_compact_document(body_html: str) -> str:
  return (
    "<!DOCTYPE html><html><head><meta charset='utf-8'/></head>"
    f"<body>{body_html}</body></html>"
  )


def raw_html_sibling(path: Path) -> Path:
  """후처리 전 원본 HTML 백업 경로 (foo.html → foo.raw.html)."""
  return path.with_suffix(".raw.html")


def postprocess_html_file(path: Path, *, save_raw: bool = True) -> tuple[int, int]:
  """파일 in-place 후처리. (table_count, paragraph_count) 반환."""
  raw = path.read_text(encoding="utf-8", errors="replace")
  before = len(raw)
  if save_raw:
    raw_path = raw_html_sibling(path)
    if not raw_path.exists():
      raw_path.write_text(raw, encoding="utf-8")
  compact_body = compact_html(raw)
  full = wrap_compact_document(compact_body)
  path.write_text(full, encoding="utf-8")
  table_count = full.count("<table")
  paragraph_count = full.count("<p")
  logger.info(
    "HTML 후처리 path=%s size %d→%d tables=%d",
    path.name,
    before,
    len(full),
    table_count,
  )
  return table_count, paragraph_count


def _collapse_empty_nodes(root: Tag) -> None:
  for tag in list(root.find_all(["p", "td", "th", "tr"])):
    if not isinstance(tag, Tag):
      continue
    text = tag.get_text(strip=True)
    if not text and not tag.find("table"):
      if tag.name == "tr":
        tag.decompose()
      elif tag.name == "p":
        tag.decompose()


def _normalize_whitespace(root: Tag) -> None:
  for node in root.find_all(string=True):
    if node.parent and node.parent.name in ("script", "style"):
      continue
    fixed = re.sub(r"[ \t]+\n", "\n", str(node))
    fixed = re.sub(r"\n{3,}", "\n\n", fixed)
    if fixed != node:
      node.replace_with(fixed)
