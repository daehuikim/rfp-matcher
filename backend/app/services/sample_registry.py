from __future__ import annotations

import json
import logging
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def load_samples_manifest(path: Path) -> tuple[list[str], dict[str, str], list[str]]:
    """manifest에서 (정렬 순서, 파일명→표시 라벨, 홈 그리드 featured) 로드."""
    if not path.is_file():
        return [], {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("samples manifest 로드 실패 path=%s: %s", path, e)
        return [], {}, []
    order = [_nfc(x) for x in (data.get("order") or [])]
    labels = {_nfc(k): v for k, v in (data.get("labels") or {}).items()}
    featured_raw = data.get("featured") or order[:6]
    featured = [_nfc(x) for x in featured_raw]
    return order, labels, featured


def sample_display_name(filename: str, labels: dict[str, str]) -> str:
    key = _nfc(filename)
    if key in labels:
        return labels[key]
    stem = Path(filename).stem
    if stem.startswith("(") and ")" in stem:
        org = stem[1 : stem.index(")")]
        rest = stem[stem.index(")") + 1 :].strip(" ._-")
        return f"{org} · {rest}" if rest else org
    return stem.replace("_", " ")


def sort_sample_names(names: list[str], order: list[str]) -> list[str]:
    rank = {name: i for i, name in enumerate(order)}
    return sorted(names, key=lambda n: (rank.get(_nfc(n), len(order)), _nfc(n).lower()))


def resolve_sample_path(raw_dir: Path, name: str) -> Path | None:
    """data/raw에서 샘플 파일 경로를 찾는다. macOS NFD/NFC 파일명 차이를 흡수."""
    direct = raw_dir / name
    if direct.is_file() and raw_dir.resolve() in direct.resolve().parents:
        return direct
    target = _nfc(name)
    for p in raw_dir.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        if _nfc(p.name) == target and raw_dir.resolve() in p.resolve().parents:
            return p
    return None
