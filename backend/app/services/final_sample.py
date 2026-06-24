"""Final 4-sample registry — FE 시연용 v3-final 파이프라인 매핑."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.sample_registry import _nfc

logger = logging.getLogger(__name__)


def load_final_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"samples": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("final manifest 로드 실패 path=%s: %s", path, e)
        return {"samples": []}


def resolve_final_sample(
    *,
    sample_name: str | None = None,
    manifest_path: Path,
    repo_root: Path,
) -> dict[str, Any] | None:
    """samples.manifest 파일명 → final.manifest 샘플 엔트리."""
    if not sample_name:
        return None
    data = load_final_manifest(manifest_path)
    key = _nfc(sample_name)
    for entry in data.get("samples") or []:
        if _nfc(entry.get("sample_name") or "") == key:
            return _resolve_paths(entry, repo_root, data)
    return None


def _resolve_paths(entry: dict[str, Any], repo_root: Path, data: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    artifacts_root = data.get("artifacts_root") or "data/artifacts_final"
    out["artifacts_dir"] = repo_root / artifacts_root / entry["id"]
    for field in ("source", "gold_xlsx"):
        raw = entry.get(field)
        if raw:
            p = Path(raw)
            out[field] = p if p.is_absolute() else repo_root / raw
    return out


def v3_export_pickle(sample_dir: Path) -> Path:
    return sample_dir / "v3_export.pkl"
