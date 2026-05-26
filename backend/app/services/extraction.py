from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from app.core.container import Container
from app.domain.enums import CategorySource, DocumentMime, PipelineStage
from app.domain.models import AtomicRow, Document, Requirement
from app.phase1.converters.registry import select_converter
from app.phase1.extraction.atomization import AtomizationCoordinator, AtomizationStrategy
from app.phase1.extraction.category_canonicalizer import build_category_canonicalizer
from app.phase1.extraction.category_provenance import (
    build_extraction_metadata,
    resolve_category_source,
    resolve_subcategory_source,
)
from app.phase1.extraction.classifier import select_classifier
from app.phase1.extraction.table_locator import TableLocator
from app.phase1.loaders.base import select_loader
from app.phase1.extraction.parsing import atom_title
from app.services.artifact_cache import ArtifactCache
from app.services.pipeline import Pipeline

logger = logging.getLogger(__name__)


class ExtractionService:
    """
    업로드된 파일 → HTML 변환 → 조견표 탐지 → atomic 분해 → Requirement 저장.

    파이프라인 단계 전이는 EventBus로 publish. 호출 측은 SSE/이벤트로 진행률을 listen.
    """

    def __init__(self, container: Container) -> None:
        self._c = container
        self._pipeline = Pipeline(container.event_bus)

    async def prepare(self, src_path: Path) -> Document:
        """파일을 Document로 등록하고 UPLOADED 이벤트만 publish — doc_id를 즉시 반환."""
        loader = select_loader(src_path)
        document = await loader.load(src_path)
        digest = ArtifactCache.file_digest(src_path)
        document = document.model_copy(update={"content_hash": digest})
        await self._c.repo.save_document(document)
        await self._pipeline.emit(document.id, PipelineStage.UPLOADED)
        return document

    async def run(self, document: Document) -> str:
        """이미 등록된 Document에 대해 변환→탐지→분해→Requirement 저장 파이프라인 수행."""

        cache = ArtifactCache(self._c.settings.artifact_cache_dir)
        if cache.has_extraction(document.content_hash):
            try:
                await cache.restore_extraction(self._c, document, fast=True)
                restored_recs = await cache.restore_recommendations(self._c, document, fast=True)
                reqs = await self._c.repo.list_requirements(document.id)
                if restored_recs < len(reqs):
                    asyncio.create_task(self._recommend_background(document.id, use_cache=True))
                return document.id
            except Exception:
                logger.warning("캐시 복원 실패 — 전체 파이프라인 재실행", exc_info=True)

        try:
            tracker = self._c.llm_tracker(document.id)
            llm_meta = {
                "llm_provider": self._c.settings.llm_provider,
                "llm_model": self._c.active_llm_model(),
            }

            # 1) HTML 변환 — 컨버터는 lifespan singleton에서 빌려 쓴다 (재생성 금지)
            await self._pipeline.emit(document.id, PipelineStage.CONVERTING, payload=llm_meta)
            if document.mime == DocumentMime.PDF:
                converter = self._c.pdf_converter
            else:
                converter = select_converter(document.mime, self._c.settings)
            out_dir = self._c.settings.storage_root / document.id
            html_doc = await converter.convert(document, out_dir)
            await self._pipeline.emit(
                document.id,
                PipelineStage.CONVERTED,
                payload={"tables": html_doc.table_count, "paragraphs": html_doc.paragraph_count},
            )

            # 2) 조견표 탐지
            await self._pipeline.emit(document.id, PipelineStage.LOCATING)
            locator = TableLocator(self._c.llm, tracker=tracker)
            refs = await locator.locate(document.id, html_doc.html_path)
            await self._pipeline.emit(
                document.id, PipelineStage.LOCATED, payload={"tables": len(refs)}
            )

            # 3) atomic 분해 — 조견표 → 섹션 → 단락 폴백
            await self._pipeline.emit(document.id, PipelineStage.ATOMIZING)
            coordinator = AtomizationCoordinator(self._c.llm, tracker=tracker)
            atom_result = await coordinator.atomize(document.id, html_doc.html_path, refs)
            atoms = atom_result.atoms
            await self._pipeline.emit(
                document.id,
                PipelineStage.ATOMIZED,
                payload={
                    "atoms": len(atoms),
                    "strategy": atom_result.strategy.value,
                    "tables": atom_result.tables_used,
                    "sections": atom_result.sections_used,
                },
            )

            # 4) 분류 — category_raw 추출 (canonicalize는 다음 스텝)
            await self._pipeline.emit(document.id, PipelineStage.CLASSIFYING)
            classifier = select_classifier(atoms, self._c.llm)
            raw_categories = await classifier.classify(atoms)
            await self._pipeline.emit(
                document.id,
                PipelineStage.CLASSIFIED,
                payload={
                    "classifier": type(classifier).__name__,
                    "distinct_categories": len({c or "기타" for c in raw_categories}),
                    "llm_usage": tracker.to_dict(),
                    **llm_meta,
                },
            )

            # 5) 분류 canonicalization — 별칭·절단·문서 전체 병합·taxonomy
            await self._pipeline.emit(document.id, PipelineStage.CANONICALIZING)
            canonicalizer = build_category_canonicalizer(self._c.settings.category_taxonomy_path)
            canon_result = canonicalizer.canonicalize(raw_categories)
            subcategories = canon_result.labels
            await self._pipeline.emit(
                document.id,
                PipelineStage.CANONICALIZED,
                payload={
                    "raw_distinct": canon_result.raw_distinct,
                    "canonical_distinct": canon_result.canonical_distinct,
                    "steps": canon_result.steps_applied,
                    "merge_count": len(canon_result.merge_map),
                },
            )

            # 6) Requirement로 변환 — 한 줄씩 저장·이벤트 (UI에서 조견표가 순차 표시)
            extraction_meta = build_extraction_metadata(
                strategy=atom_result.strategy,
                table_headers=[r.header_columns for r in refs],
            )
            has_category_column = any(r.has_category_column for r in refs)
            document = document.model_copy(update={"extraction_meta": extraction_meta})
            await self._c.repo.save_document(document)

            requirements = self._to_requirements(
                document.id,
                atoms,
                subcategories,
                strategy=atom_result.strategy,
                has_category_column=has_category_column,
            )
            total = len(requirements)
            for i, req in enumerate(requirements):
                await self._c.repo.append_requirement(document.id, req)
                await self._pipeline.emit(
                    document.id,
                    PipelineStage.ATOMIZING,
                    payload={
                        "done": i + 1,
                        "total": total,
                        "requirement_id": req.id,
                        "snippet": f"조견표 {i + 1}/{total} · {(req.detail or req.name)[:48]}…",
                    },
                )
            await self._pipeline.emit(
                document.id,
                PipelineStage.READY_FOR_REVIEW,
                payload={
                    "requirements": total,
                    "snippet": f"조견표 {total}줄 추출 완료 — Excel·검토 가능",
                    "llm_usage": tracker.to_dict(),
                    **llm_meta,
                },
            )
            cache.save_extraction(
                document=document,
                requirements=requirements,
                html_path=html_doc.html_path,
                extraction_meta=extraction_meta,
                event_bus=self._c.event_bus,
            )
        except Exception as e:  # noqa: BLE001
            await self._pipeline.emit_failed(document.id, PipelineStage.READY_FOR_REVIEW, str(e))
            raise

        # 추출이 끝나면 곧바로 AI 추천을 백그라운드로 시작. 사용자는 이미 조견표를 보고
        # Excel로 내려받거나 사람 판정을 시작할 수 있고, 추천은 카드별로 도착하는 대로 갱신된다.
        asyncio.create_task(self._recommend_background(document.id, use_cache=True))

        return document.id

    async def _recommend_background(self, doc_id: str, *, use_cache: bool) -> None:
        from app.services.recommendation import RecommendationService

        try:
            doc = self._c.repo.documents.get(doc_id)
            if not doc:
                return
            cache = ArtifactCache(self._c.settings.artifact_cache_dir)
            if use_cache and doc.content_hash and cache.has_any_recommendations(doc.content_hash):
                await cache.restore_recommendations(self._c, doc, fast=False)

            reqs, recs, _ = await self._c.repo.snapshot(doc_id)
            missing = [r for r in reqs if r.id not in recs]
            if not missing:
                logger.info("skip recommend doc=%s — already %d/%d", doc_id, len(recs), len(reqs))
                return
            await RecommendationService(self._c).recommend_requirements(doc_id, missing)
        except Exception:
            logger.exception("백그라운드 추천 실패 doc=%s", doc_id)

    @staticmethod
    def _to_requirements(
        doc_id: str,
        atoms: list[AtomicRow],
        subcategories: list[str],
        *,
        strategy: AtomizationStrategy,
        has_category_column: bool,
    ) -> list[Requirement]:
        """
        atomic → Requirement.

        category: category_raw 또는 구조·추론 분류
        subcategory: canonicalize 소분류 (본문과 다를 때만 저장)
        """
        if len(subcategories) != len(atoms):
            raise ValueError(f"subcategories({len(subcategories)})와 atoms({len(atoms)}) 길이 불일치")
        out: list[Requirement] = []
        counts: dict[str, int] = {}
        for atom, sub in zip(atoms, subcategories, strict=True):
            group = _requirement_group(atom.category_raw)
            cat_source = resolve_category_source(
                atom,
                strategy=strategy,
                has_category_column=has_category_column,
            )
            canon = (sub or "").strip() or "기타"
            category, subcategory, sub_source = _resolve_category_labels(group, canon)
            counts[category] = counts.get(category, 0) + 1
            code = f"{_code_prefix(category)}-{counts[category]:03d}"
            name = atom_title(atom.text)
            out.append(
                Requirement(
                    id=uuid.uuid4().hex,
                    doc_id=doc_id,
                    category=group,
                    subcategory=subcategory,
                    category_source=cat_source,
                    subcategory_source=sub_source,
                    code=code,
                    name=name,
                    detail=atom.text,
                    source_atomic_id=None,
                    source_page=atom.source_page,
                    source_section=atom.source_section,
                    source_table_index=atom.table_index,
                )
            )
        return out


def _resolve_category_labels(
    group: str,
    canon: str,
) -> tuple[str, str | None, CategorySource | None]:
    """
    canonicalizer 결과를 category/subcategory에 반영.

    니터링→모니터링처럼 raw가 잘린 경우 canon을 category로 승격.
    """
    if canon != "기타" and canon != group:
        return canon, None, None
    category = group
    subcategory = None if canon in ("", "기타", group) else canon
    sub_source = resolve_subcategory_source(subcategory) if subcategory else None
    return category, subcategory, sub_source


def _requirement_group(category_raw: str | None) -> str:
    if category_raw and category_raw.strip():
        return " ".join(category_raw.split())
    return "미분류"


def _code_prefix(category: str) -> str:
    # 첫 4글자(영문자/숫자만) — 분류 없으면 "REQ"
    cleaned = "".join(ch for ch in category if ch.isalnum())
    return cleaned[:4].upper() or "REQ"
