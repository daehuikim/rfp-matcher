"""V2/V3 prototype 추출 결과(pickle) — 웹 export·요건 보강용."""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, Settings
from app.domain.models import Document, Requirement


def _pickle_candidates(settings: Settings, doc_id: str, doc: Document | None) -> list[Path]:
    out = [
        settings.storage_root / doc_id / "v3_export.pkl",
        settings.storage_root / doc_id / "v2_export.pkl",
    ]
    if doc is not None and doc.content_hash:
        bucket = settings.artifact_cache_dir / doc.content_hash[:16]
        out.extend([bucket / "v3_export.pkl", bucket / "v2_export.pkl"])
    return out


def load_native_export(
    settings: Settings,
    doc_id: str,
    doc: Document | None = None,
) -> dict[str, Any] | None:
    """v3/v2_export.pkl payload — reqs·overview·strategy."""
    pkl = next((p for p in _pickle_candidates(settings, doc_id, doc) if p.is_file()), None)
    if pkl is None:
        return None
    try:
        payload = pickle.loads(pkl.read_bytes())
    except Exception:  # noqa: BLE001
        return None
    if not payload.get("reqs"):
        return None
    payload["_source_pkl"] = str(pkl)
    return payload


def rel_asset_path(path: str) -> str:
    """절대 경로 → repo 상대 경로(자산 API용)."""
    p = Path(path)
    if not p.is_absolute():
        return path.lstrip("/")
    try:
        return str(p.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return path


def resolve_asset_path(settings: Settings, doc_id: str, doc: Document | None, rel: str) -> Path | None:
    """허용된 루트 아래 자산 파일만 resolve."""
    raw = Path(rel)
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (PROJECT_ROOT / raw).resolve()

    allowed_roots: list[Path] = [
        PROJECT_ROOT.resolve(),
        settings.storage_root.resolve(),
        settings.artifacts_final_dir.resolve(),
        settings.artifact_cache_dir.resolve(),
    ]
    if not any(candidate == root or root in candidate.parents for root in allowed_roots):
        return None
    if not candidate.is_file():
        return None
    return candidate


def enrich_requirements(
    settings: Settings,
    doc_id: str,
    doc: Document | None,
    reqs: list[Requirement],
) -> list[Requirement]:
    """in-memory Requirement에 pickle의 detail_images·detail 보강."""
    payload = load_native_export(settings, doc_id, doc)
    if payload is None:
        return reqs
    native_by_rid = {getattr(r, "rid", ""): r for r in payload.get("reqs") or []}
    out: list[Requirement] = []
    for req in reqs:
        native = native_by_rid.get(req.code)
        if native is None:
            out.append(req)
            continue
        updates: dict[str, Any] = {}
        imgs = [rel_asset_path(p) for p in getattr(native, "detail_images", []) or []]
        if imgs and not req.detail_images:
            updates["detail_images"] = imgs
        native_detail = (getattr(native, "detail", "") or "").strip()
        if native_detail and req.detail in ("[표]", "[관련 화면(안)]") and req.detail == native_detail:
            pass  # placeholder — images carry content
        elif native_detail and len(native_detail) > len(req.detail or ""):
            updates["detail"] = native_detail
        if updates:
            out.append(req.model_copy(update=updates))
        else:
            out.append(req)
    return out


def write_native_excel(
    settings: Settings,
    doc_id: str,
    doc: Document | None,
    out_path: Path,
    *,
    app_reqs: list[Requirement] | None = None,
    recommendations: dict | None = None,
    judgements: dict | None = None,
) -> bool:
    """prototype excel_writer — 표안표·이미지 포함 원문순서 조견표."""
    payload = load_native_export(settings, doc_id, doc)
    if payload is None:
        return False

    from prototype.v3.financial_excel_writer import write_financial_excel
    from prototype.v2.excel_writer import write_excel

    reqs = payload["reqs"]
    overview = payload.get("overview")
    strategy = payload.get("strategy") or ""

    # 추천·사람판정 매핑. pkl 요건과 앱 요건은 1:1 동일 순서이므로 객체 id로 매핑한다.
    # (rid는 같은 표에서 중복될 수 있어 rid 키로 매핑하면 판정이 덮어써짐 → id로 행 단위 정확 매칭)
    ai_by_rid: dict | None = None
    if app_reqs is not None and (recommendations or judgements):
        recs = recommendations or {}
        juds = judgements or {}
        if len(app_reqs) == len(reqs):
            ai_by_rid = {
                id(v3r): (recs.get(ar.id), juds.get(ar.id))
                for v3r, ar in zip(reqs, app_reqs)
            }
        else:  # 길이 불일치 폴백 — rid 첫 매칭(덜 정확)
            by_rid: dict = {}
            for ar in app_reqs:
                by_rid.setdefault(ar.code, (recs.get(ar.id), juds.get(ar.id)))
            ai_by_rid = {
                id(v3r): by_rid.get(getattr(v3r, "rid", "") or "", (None, None))
                for v3r in reqs
            }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # reqs 에 levels 가 채워져 있으면 동적 칼럼 writer(금융/비정형 공통 경로 — 섹션→계위, 고정칼럼 없음).
    # 없으면 기존: 금융(table_faithful/cell_llm)→financial, 공공→기본 writer.
    if any(getattr(r, "levels", None) for r in reqs):
        from prototype.v3.dynamic_excel_writer import write_dynamic_excel

        write_dynamic_excel(reqs, out_path, overview=overview, tab_order=None, ai_by_rid=ai_by_rid)
    elif strategy in ("table_faithful", "cell_llm"):
        write_financial_excel(reqs, out_path, overview=overview, tab_order=None, ai_by_rid=ai_by_rid)
    else:
        write_excel(reqs, out_path, overview=overview, tab_order=None, ai_by_rid=ai_by_rid)
    return True
