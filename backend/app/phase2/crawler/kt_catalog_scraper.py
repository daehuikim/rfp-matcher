"""
KT K intelligence Suite 카탈로그 1회 크롤러 (CLI).

조사 결과(2026-05): ai.kt.com은 정적 페이지(SPA 아님). Playwright 불필요.
sitemap.xml 기반 `/solution/*` 경로 6개 + `/usecase`, `/main`을 시드 URL로 사용한다.

기본 정책:
  - 운영 시스템에 상주하지 않는다 — 별도 1회성 스크립트.
  - 출력은 `data/catalog/kt_solutions.json` (사람 검수 후 그대로 커밋).
  - 사이트 DOM 변경에 약하므로 셀렉터/필드를 한 곳(`SELECTORS`)에 모았다.

사용:
  python -m app.phase2.crawler.kt_catalog_scraper --out data/catalog/kt_solutions.json
  python -m app.phase2.crawler.kt_catalog_scraper --seed   # 네트워크 없이 합성 시드만
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, Tag

from app.phase2.catalog.store import CatalogEntry, CatalogStore
from app.services.catalog_indexer import synthesize_seed_catalog

logger = logging.getLogger(__name__)

# 사이트 조사 결과(robots.txt: 전 영역 허용, sitemap.xml 검증) 기반 시드 URL.
# `/solution/RAG`·`/solution/RAI`는 대문자가 canonical (소문자도 동작은 함).
KT_SEED_URLS: list[tuple[str, str]] = [
    ("K Model", "https://ai.kt.com/solution/model"),
    ("K RAG", "https://ai.kt.com/solution/RAG"),
    ("K Agent", "https://ai.kt.com/solution/agent"),
    ("K Studio", "https://ai.kt.com/solution/studio"),
    ("K RAI", "https://ai.kt.com/solution/RAI"),
    ("K SPC", "https://ai.kt.com/solution/cloud"),
    ("Use Case", "https://ai.kt.com/usecase"),
]

# DOM 변경에 약한 부분은 여기 한 곳만 손보면 된다.
# 첫 크롤 때 HTML dump해서 실제 클래스명으로 좁힐 것.
SELECTORS = {
    "section": "section, div.section",
    "card": "article, li, div[class*='card'], div[class*='item'], div[class*='solution']",
    "title": "h2, h3, h4, [class*='title'], [class*='name']",
    "description": "p, [class*='desc'], [class*='summary']",
    "strength": "[class*='strength'] li, [class*='benefit'] li",
    "reference": "[class*='case'] li, [class*='reference'] li, a[href*='/usecase']",
}

USER_AGENT = "rfp-matcher-catalog-bot/0.1 (+contact: 내부망 운영자)"


async def scrape_with_httpx(
    urls: list[tuple[str, str]] | None = None,
) -> list[CatalogEntry]:
    """정적 HTTP 크롤. 셀렉터 변경 영향은 SELECTORS 한 곳에서만."""
    targets = urls or KT_SEED_URLS
    entries: list[CatalogEntry] = []
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=20.0,
        follow_redirects=True,
    ) as client:
        for major, url in targets:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning("크롤 실패 %s: %s", url, e)
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            entries.extend(_parse_page(major, url, soup))
    if not entries:
        logger.warning("크롤 결과 0건 — SELECTORS 점검 필요")
    return entries


def _parse_page(major: str, source_url: str, soup: BeautifulSoup) -> list[CatalogEntry]:
    """한 페이지의 카드들을 CatalogEntry로 변환. 너무 잡다한 노드는 제목 없으면 스킵."""
    out: list[CatalogEntry] = []
    seen_ids: set[str] = set()
    for card in soup.select(SELECTORS["card"]):
        if not isinstance(card, Tag):
            continue
        title_node = card.select_one(SELECTORS["title"])
        title = title_node.get_text(" ", strip=True) if title_node else ""
        if not title or len(title) < 2:
            continue
        desc_node = card.select_one(SELECTORS["description"])
        desc = desc_node.get_text(" ", strip=True) if desc_node else ""
        if len(desc) < 8 and len(title) < 6:
            # 헤더·메뉴 같은 잡음 제외
            continue
        strengths = [
            li.get_text(" ", strip=True)
            for li in card.select(SELECTORS["strength"])
            if li.get_text(strip=True)
        ]
        refs = [
            li.get_text(" ", strip=True)
            for li in card.select(SELECTORS["reference"])
            if li.get_text(strip=True)
        ]
        cid = _slug(f"{major}-{title}")
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        out.append(
            CatalogEntry(
                id=cid,
                대분류=major,
                중분류=title,
                소분류=title,
                솔루션명=f"{major} · {title}",
                설명=desc or title,
                강점=strengths,
                한계=[f"메타 출처: {source_url}"],
                레퍼런스=refs,
            )
        )
    return out


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in s).strip("-").lower()


async def _async_main(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    if args.seed:
        entries = synthesize_seed_catalog()
        logger.info("합성 시드 %d건", len(entries))
    else:
        entries = await scrape_with_httpx()
        if not entries:
            logger.error("크롤 결과 0건 — DOM 변경 가능성. --seed로 폴백 권장.")
            return 2
    store = CatalogStore(out)
    store.replace(entries)
    store.save()
    print(f"saved {len(store)} entries -> {out}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s :: %(message)s")
    parser = argparse.ArgumentParser(description="KT K intelligence Suite 카탈로그 크롤러")
    parser.add_argument("--out", required=True, help="결과 JSON 경로")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="네트워크 없이 합성 시드만 생성 (CI/오프라인 PoC)",
    )
    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
