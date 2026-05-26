from __future__ import annotations

from app.core.config import get_settings
from app.phase2.catalog.store import CatalogEntry, CatalogStore


def synthesize_seed_catalog() -> list[CatalogEntry]:
    """테스트·시드용 — data/catalog/kt_solutions.json 로드."""
    path = get_settings().catalog_path
    if path.exists():
        return CatalogStore.load(path).entries
    return []
