from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.domain.enums import PipelineStage
from app.domain.models import Document, ExtractionMetadata, HtmlDoc, Recommendation, Requirement
from app.services.pipeline import Pipeline
from app.services.pipeline_timing import capture_pipeline_snapshot, restore_pipeline_snapshot
from app.phase1.extraction.category_canonicalizer import build_category_canonicalizer

if TYPE_CHECKING:
    from app.core.container import Container
    from app.services.event_bus import EventBus

logger = logging.getLogger(__name__)

# 파이프라인 로직이 바뀌면 bump → 기존 캐시 무효
CACHE_VERSION = "14"
RECOMMENDATION_CACHE_VERSION = "2"


def _normalize_hash(value: str) -> str:
    return value.strip().lower()


class ArtifactCache:
    """
    원본 파일(content_hash) 기준 디스크 캐시.

    data/artifacts/<sha256[:16]>/
      manifest.json
      converted.html
      requirements.json
      recommendations.json  — AI+rubric (requirement_code로 요건과 연결)
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def file_digest(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _bucket(self, content_hash: str) -> Path:
        return self._root / content_hash[:16]

    def has_extraction(self, content_hash: str | None) -> bool:
        if not content_hash:
            return False
        d = self._bucket(content_hash)
        manifest = d / "manifest.json"
        if not manifest.is_file() or not (d / "requirements.json").is_file():
            return False
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return data.get("cache_version") == CACHE_VERSION
        except json.JSONDecodeError:
            return False

    def load_manifest(self, content_hash: str) -> dict[str, Any] | None:
        """content_hash(전체·16자 prefix)로 manifest 로드 — 유효 캐시만."""
        if not content_hash:
            return None
        token = _normalize_hash(content_hash)
        if len(token) <= 16:
            bucket = self._root / token[:16]
            manifest_path = bucket / "manifest.json"
            if manifest_path.is_file():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    return None
                if data.get("cache_version") == CACHE_VERSION and (bucket / "requirements.json").is_file():
                    return data
            return None
        d = self._bucket(token)
        manifest_path = d / "manifest.json"
        if not manifest_path.is_file() or not (d / "requirements.json").is_file():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if data.get("cache_version") != CACHE_VERSION:
            return None
        return data

    def list_cached_projects(self) -> list[dict[str, Any]]:
        """디스크 artifacts 목록 — 사이드바·재오픈용."""
        out: list[dict[str, Any]] = []
        if not self._root.is_dir():
            return out
        for bucket_dir in sorted(self._root.iterdir()):
            if not bucket_dir.is_dir():
                continue
            manifest_path = bucket_dir / "manifest.json"
            if not manifest_path.is_file() or not (bucket_dir / "requirements.json").is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if manifest.get("cache_version") != CACHE_VERSION:
                continue
            content_hash = str(manifest.get("content_hash") or "")
            if not content_hash:
                continue
            source_name = manifest.get("source_name")
            title = Path(str(source_name)).stem if source_name else bucket_dir.name
            out.append(
                {
                    "bucket": bucket_dir.name,
                    "content_hash": content_hash,
                    "source_name": source_name,
                    "title": title,
                    "requirements_count": int(manifest.get("requirements_count") or 0),
                    "has_recommendations": bool(manifest.get("has_recommendations")),
                    "recommendation_count": self.count_cached_recommendations(content_hash),
                    "pipeline_snapshot": manifest.get("pipeline_snapshot"),
                }
            )
        return out

    def _load_recommendations_payload(self, d: Path) -> dict[str, Any] | None:
        rec_path = d / "recommendations.json"
        if not rec_path.is_file():
            return None
        try:
            raw = json.loads(rec_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if isinstance(raw, list):
            return None
        if raw.get("recommendation_cache_version") != RECOMMENDATION_CACHE_VERSION:
            return None
        return raw

    def count_cached_recommendations(self, content_hash: str | None) -> int:
        """디스크에 저장된 AI 검토 건수 — 부분 캐시 포함."""
        if not content_hash:
            return 0
        payload = self._load_recommendations_payload(self._bucket(content_hash))
        if not payload:
            return 0
        return len(payload.get("items") or [])

    def has_recommendations(self, content_hash: str | None) -> bool:
        """요건 수와 AI 검토 수가 일치할 때만 True — 전량 캐시 히트."""
        if not content_hash:
            return False
        d = self._bucket(content_hash)
        payload = self._load_recommendations_payload(d)
        if not payload:
            return False
        items = payload.get("items") or []
        if not items:
            return False
        manifest_path = d / "manifest.json"
        if not manifest_path.is_file():
            return len(items) > 0
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        req_count = manifest.get("requirements_count")
        rec_req_count = payload.get("requirements_count")
        if req_count is None or rec_req_count != req_count:
            return False
        return manifest.get("has_recommendations") is True and len(items) >= req_count

    def has_any_recommendations(self, content_hash: str | None) -> bool:
        return self.count_cached_recommendations(content_hash) > 0

    def save_extraction(
        self,
        *,
        document: Document,
        requirements: list[Requirement],
        html_path: Path,
        extraction_meta: ExtractionMetadata | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        if not document.content_hash:
            return
        d = self._bucket(document.content_hash)
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(html_path, d / "converted.html")

        req_path = d / "requirements.json"
        old_codes: set[str] = set()
        if req_path.is_file():
            try:
                for item in json.loads(req_path.read_text(encoding="utf-8")):
                    code = item.get("code")
                    if code:
                        old_codes.add(code)
            except json.JSONDecodeError:
                old_codes = set()

        new_codes = {r.code for r in requirements if r.code}
        codes_changed = bool(old_codes) and old_codes != new_codes

        req_path.write_text(
            json.dumps([r.model_dump(mode="json") for r in requirements], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        rec_path = d / "recommendations.json"
        if rec_path.is_file():
            payload = self._load_recommendations_payload(d)
            if codes_changed or payload is None:
                rec_path.unlink()

        has_recs = self.has_recommendations(document.content_hash)
        manifest = {
            "cache_version": CACHE_VERSION,
            "content_hash": document.content_hash,
            "source_name": document.source_filename or document.src_path.name,
            "mime": document.mime.value,
            "requirements_count": len(requirements),
            "has_recommendations": has_recs,
        }
        if extraction_meta:
            manifest["extraction_meta"] = extraction_meta.model_dump(mode="json")
        if event_bus is not None:
            manifest["pipeline_snapshot"] = capture_pipeline_snapshot(event_bus, document.id)
        (d / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "아티팩트 저장(추출): %s — %d requirements → %s",
            document.src_path.name,
            len(requirements),
            d,
        )

    def update_pipeline_snapshot(
        self,
        *,
        document: Document,
        event_bus: EventBus,
    ) -> None:
        """AI 완료 등 후 manifest의 pipeline_snapshot 갱신."""
        if not document.content_hash:
            return
        d = self._bucket(document.content_hash)
        manifest_path = d / "manifest.json"
        if not manifest_path.is_file():
            return
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["pipeline_snapshot"] = capture_pipeline_snapshot(event_bus, document.id)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_recommendations(
        self,
        *,
        document: Document,
        recommendations: list[Recommendation],
        event_bus: EventBus | None = None,
    ) -> None:
        self.merge_save_recommendations(
            document=document,
            recommendations=recommendations,
            event_bus=event_bus,
            replace=True,
        )

    def merge_save_recommendations(
        self,
        *,
        document: Document,
        recommendations: list[Recommendation],
        event_bus: EventBus | None = None,
        replace: bool = False,
    ) -> int:
        """AI 검토를 requirement_code 기준으로 병합 저장 — 배치마다 부분 캐시 가능."""
        if not document.content_hash or not recommendations:
            return self.count_cached_recommendations(document.content_hash)
        d = self._bucket(document.content_hash)
        if not d.is_dir():
            return 0

        code_by_id: dict[str, str] = {}
        req_path = d / "requirements.json"
        if req_path.is_file():
            for item in json.loads(req_path.read_text(encoding="utf-8")):
                code_by_id[item["id"]] = item.get("code", "")

        existing_payload = self._load_recommendations_payload(d)
        merged_by_code: dict[str, dict[str, Any]] = {}
        if not replace and existing_payload:
            for item in existing_payload.get("items") or []:
                code = item.get("requirement_code") or ""
                if code:
                    merged_by_code[code] = item

        for rec in recommendations:
            item = rec.model_dump(mode="json")
            code = code_by_id.get(rec.requirement_id, "")
            item["requirement_code"] = code
            if code:
                merged_by_code[code] = item
            else:
                merged_by_code[f"__id__:{rec.requirement_id}"] = item

        items = list(merged_by_code.values())
        req_count = len(code_by_id) or len(items)
        payload = {
            "recommendation_cache_version": RECOMMENDATION_CACHE_VERSION,
            "requirements_count": req_count,
            "items": items,
        }
        (d / "recommendations.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifest_path = d / "manifest.json"
        complete = len(items) >= req_count and req_count > 0
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["has_recommendations"] = complete
            manifest["recommendation_cache_version"] = RECOMMENDATION_CACHE_VERSION
            manifest["recommendations_count"] = len(items)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if event_bus is not None:
            manifest_path = d / "manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["pipeline_snapshot"] = capture_pipeline_snapshot(event_bus, document.id)
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        logger.info(
            "아티팩트 저장(AI): %s — %d recommendations (complete=%s) → %s",
            document.src_path.name,
            len(items),
            complete,
            d,
        )
        return len(items)

    async def restore_full(self, container: Container, document: Document) -> HtmlDoc:
        """추출+AI+rubric 캐시를 한 번에 복원."""
        html_doc = await self.restore_extraction(container, document, fast=True)
        await self.restore_recommendations(container, document, fast=True)
        return html_doc

    async def restore_extraction(
        self,
        container: Container,
        document: Document,
        *,
        fast: bool = False,
    ) -> HtmlDoc:
        """캐시에서 HTML·requirements 복원 — 변환/LLM 추출 스킵."""
        if not document.content_hash:
            raise ValueError("content_hash 없음")
        d = self._bucket(document.content_hash)
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("cache_version") != CACHE_VERSION:
            raise ValueError(f"캐시 버전 불일치: {manifest.get('cache_version')}")

        out_dir = container.settings.storage_root / document.id
        out_dir.mkdir(parents=True, exist_ok=True)
        html_dest = out_dir / f"{document.id}.html"
        shutil.copy2(d / "converted.html", html_dest)

        raw_reqs = json.loads((d / "requirements.json").read_text(encoding="utf-8"))
        requirements: list[Requirement] = []
        for item in raw_reqs:
            req = Requirement.model_validate(item)
            requirements.append(req.model_copy(update={"doc_id": document.id}))
        requirements = _fix_cached_categories(requirements)

        extraction_meta: ExtractionMetadata | None = None
        if manifest.get("extraction_meta"):
            extraction_meta = ExtractionMetadata.model_validate(manifest["extraction_meta"])
            document = document.model_copy(update={"extraction_meta": extraction_meta})
        source_name = manifest.get("source_name")
        if source_name and not document.source_filename:
            document = document.model_copy(update={"source_filename": str(source_name)})
        await container.repo.save_document(document)

        pipeline = Pipeline(container.event_bus)
        cached = {"cached": True}
        snapshot = manifest.get("pipeline_snapshot")

        total = len(requirements)
        if restore_pipeline_snapshot(container.event_bus, document.id, snapshot):
            if fast:
                await container.repo.save_requirements(document.id, requirements)
            else:
                for i, req in enumerate(requirements):
                    await container.repo.append_requirement(document.id, req)
            logger.info(
                "캐시 복원(추출·타이밍): doc=%s hash=%s… %d건 fast=%s total_ms=%s",
                document.id,
                document.content_hash[:16],
                total,
                fast,
                container.event_bus.total_elapsed_ms(document.id),
            )
            return HtmlDoc(doc_id=document.id, html_path=html_dest, table_count=0, paragraph_count=0)

        await pipeline.emit(document.id, PipelineStage.CONVERTING)
        await pipeline.emit(
            document.id,
            PipelineStage.CONVERTED,
            payload={"tables": 0, "paragraphs": 0, **cached},
        )
        await pipeline.emit(document.id, PipelineStage.LOCATING)
        await pipeline.emit(document.id, PipelineStage.LOCATED, payload={"tables": 0, **cached})
        await pipeline.emit(document.id, PipelineStage.ATOMIZING)
        await pipeline.emit(
            document.id,
            PipelineStage.ATOMIZED,
            payload={"atoms": len(requirements), **cached},
        )
        await pipeline.emit(document.id, PipelineStage.CLASSIFYING)
        await pipeline.emit(document.id, PipelineStage.CLASSIFIED, payload=cached)
        await pipeline.emit(document.id, PipelineStage.CANONICALIZING)
        await pipeline.emit(document.id, PipelineStage.CANONICALIZED, payload=cached)

        total = len(requirements)
        if fast:
            await container.repo.save_requirements(document.id, requirements)
        else:
            for i, req in enumerate(requirements):
                await container.repo.append_requirement(document.id, req)
                await pipeline.emit(
                    document.id,
                    PipelineStage.ATOMIZING,
                    payload={
                        "done": i + 1,
                        "total": total,
                        "requirement_id": req.id,
                        "snippet": f"(캐시) 조견표 {i + 1}/{total}",
                        **cached,
                    },
                )
        await pipeline.emit(
            document.id,
            PipelineStage.READY_FOR_REVIEW,
            payload={
                "requirements": total,
                "snippet": f"(캐시) 조견표 {total}줄",
                **cached,
            },
        )
        logger.info("캐시 복원(추출): doc=%s hash=%s… %d건 fast=%s", document.id, document.content_hash[:16], total, fast)
        return HtmlDoc(doc_id=document.id, html_path=html_dest, table_count=0, paragraph_count=0)

    async def restore_recommendations(
        self,
        container: Container,
        document: Document,
        *,
        fast: bool = False,
    ) -> int:
        if not document.content_hash:
            return 0
        d = self._bucket(document.content_hash)
        payload = self._load_recommendations_payload(d)
        if not payload:
            return 0

        reqs = await container.repo.list_requirements(document.id)
        by_code = {r.code: r for r in reqs if r.code}
        by_id = {r.id: r for r in reqs}

        restored = 0
        for item in payload.get("items") or []:
            code = item.get("requirement_code") or ""
            fields = {k: v for k, v in item.items() if k != "requirement_code"}
            target_id = fields.get("requirement_id")
            if code and code in by_code:
                fields["requirement_id"] = by_code[code].id
            elif not target_id or target_id not in by_id:
                logger.warning("추천 캐시 매칭 실패 code=%s id=%s", code, target_id)
                continue
            rec = Recommendation.model_validate(fields)
            await container.repo.upsert_recommendation(rec)
            restored += 1

        if restored == 0:
            return 0

        pipeline = Pipeline(container.event_bus)
        cached = {"cached": True}
        total_reqs = len(reqs)
        manifest_path = d / "manifest.json"
        manifest_data = (
            json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        )
        snapshot = manifest_data.get("pipeline_snapshot")
        snapshot_complete = snapshot and snapshot.get("last_stage") == PipelineStage.RECOMMENDED.value

        if snapshot_complete and restore_pipeline_snapshot(container.event_bus, document.id, snapshot):
            logger.info(
                "캐시 복원(AI·타이밍): doc=%s %d/%d fast=%s total_ms=%s",
                document.id,
                restored,
                total_reqs,
                fast,
                container.event_bus.total_elapsed_ms(document.id),
            )
            return restored

        if restored >= total_reqs and total_reqs > 0:
            if fast:
                await pipeline.emit(
                    document.id,
                    PipelineStage.RECOMMENDING,
                    payload={
                        "done": restored,
                        "total": total_reqs,
                        "snippet": f"(캐시) AI 검토 {restored}/{total_reqs}",
                        **cached,
                    },
                )
            await pipeline.emit(
                document.id,
                PipelineStage.RECOMMENDED,
                payload={
                    "recommendations": restored,
                    "snippet": "(캐시) 모든 요건 AI 검토 완료",
                    **cached,
                },
            )
        else:
            await pipeline.emit(
                document.id,
                PipelineStage.RECOMMENDING,
                payload={
                    "done": restored,
                    "total": total_reqs,
                    "snippet": f"(캐시) AI 검토 {restored}/{total_reqs}",
                    **cached,
                },
            )
        logger.info(
            "캐시 복원(AI): doc=%s %d/%d fast=%s",
            document.id,
            restored,
            total_reqs,
            fast,
        )
        return restored


def _fix_cached_categories(requirements: list[Requirement]) -> list[Requirement]:
    """캐시된 요건의 잘린 분류(니터링 등)를 canonicalizer로 즉시 보정."""
    if not requirements:
        return requirements
    canon = build_category_canonicalizer()
    labels = canon.canonicalize([r.category for r in requirements]).labels
    out: list[Requirement] = []
    for req, canon_cat in zip(requirements, labels, strict=True):
        if canon_cat != "기타" and canon_cat != req.category:
            out.append(
                req.model_copy(
                    update={
                        "category": canon_cat,
                        "subcategory": None,
                        "subcategory_source": None,
                    }
                )
            )
        else:
            out.append(req)
    return out
