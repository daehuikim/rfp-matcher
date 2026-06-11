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

        # Final 4-sample — v3-final 파이프라인 (public_form / cell_llm)
        final_entry = self._resolve_final_sample(document)
        if final_entry is not None:
            return await self._run_v3_final(document, final_entry)

        # V2 엔진(results_final 산출) — opendataloader→LLM 스키마 추출
        if self._c.settings.extraction_engine == "v2":
            return await self._run_v2(document)

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

    async def _v2_input_path(self, document: Document, log_session=None) -> str:
        """V2 엔진 입력 경로.

        V2 `run()`은 .pdf(opendataloader)·.html 만 받는다. HWPX/DOC/DOCX 는
        앱 컨버터(HwpxConverter·LibreOffice·mammoth)로 HTML 변환 후 그 경로를
        넘겨 V2의 HTML 추출경로(grids_from_html)를 태운다. 법제처 hwpx 가
        이 경로로 gold recall 100%.
        """
        import shutil

        from app.phase1.converters.html_postprocess import raw_html_sibling

        src = Path(document.src_path)
        ext = src.suffix.lower()
        if ext in (".pdf", ".html", ".htm"):
            return str(src)
        out_dir = self._c.settings.storage_root / document.id / "v2_convert"
        out_dir.mkdir(parents=True, exist_ok=True)
        converter = select_converter(document.mime, self._c.settings)
        converter_name = type(converter).__name__
        html_doc = await converter.convert(document, out_dir)
        # V2가 의미있는 doc 이름을 쓰도록 원본 stem 으로 복사
        stem = Path(document.source_filename).stem if document.source_filename else document.id
        nice = out_dir / f"{stem}.html"
        if Path(html_doc.html_path) != nice:
            shutil.copyfile(html_doc.html_path, nice)
            raw_src = raw_html_sibling(Path(html_doc.html_path))
            if raw_src.is_file():
                shutil.copyfile(raw_src, raw_html_sibling(nice))
        if log_session is not None:
            raw_path = raw_html_sibling(nice)
            log_session.record_convert_raw(
                converter=converter_name,
                raw_html=raw_path if raw_path.is_file() else None,
                input_ref=src,
            )
            log_session.record_convert_postprocessed(
                post_html=nice,
                raw_html=raw_path if raw_path.is_file() else None,
                converter=converter_name,
            )
        return str(nice)

    def _resolve_final_sample(self, document: Document):
        from app.core.config import PROJECT_ROOT
        from app.services.final_sample import resolve_final_sample

        name = document.source_filename or Path(document.src_path).name
        return resolve_final_sample(
            sample_name=name,
            manifest_path=self._c.settings.final_manifest_path,
            repo_root=PROJECT_ROOT,
        )

    async def _run_v3_final(self, document: Document, entry: dict) -> str:
        """prototype/v3-final — 공공·금융 4종 시연 파이프라인."""
        import pickle

        try:
            llm_meta = {
                "llm_provider": self._c.settings.llm_provider,
                "llm_model": self._c.active_llm_model(),
            }
            await self._pipeline.emit(document.id, PipelineStage.CONVERTING, payload=llm_meta)

            sample_id = entry["id"]
            strategy = entry["strategy"]
            sample_dir = entry["artifacts_dir"]
            pkl_path = sample_dir / "v3_export.pkl"
            manifest = None

            if pkl_path.is_file():
                payload = pickle.loads(pkl_path.read_bytes())
                v2_reqs = payload.get("reqs") or []
                overview = payload.get("overview")
                steps = [f"cache: {sample_id}/v3_export.pkl ({len(v2_reqs)} rows)"]
            else:
                from prototype.v3.pipeline_final import run_sample

                source = Path(entry["source"])
                if not source.is_file():
                    source = Path(document.src_path)
                gold = entry.get("gold_xlsx")
                gold_path = Path(gold) if gold else None
                manifest = await asyncio.to_thread(
                    run_sample,
                    sample_id=sample_id,
                    source=source,
                    strategy=strategy,
                    label=entry.get("label") or sample_id,
                    out_root=self._c.settings.artifacts_final_dir,
                    gold_xlsx=gold_path if gold_path and gold_path.is_file() else None,
                )
                v2_reqs = manifest.get("_reqs") or []
                overview = manifest.get("_overview")
                steps = manifest.get("steps") or []

            await self._pipeline.emit(
                document.id,
                PipelineStage.ATOMIZED,
                payload={
                    "atoms": len(v2_reqs),
                    "strategy": strategy,
                    "engine": "v3_final",
                    **llm_meta,
                },
            )

            requirements = [self._v2_to_requirement(document.id, r) for r in v2_reqs]
            await self._c.repo.save_document(document)
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
                        "snippet": f"v3-final {i + 1}/{total} · {(req.name or req.detail)[:48]}…",
                    },
                )

            export_payload = {
                "reqs": v2_reqs,
                "overview": overview,
                "strategy": strategy,
                "sample_id": sample_id,
            }
            blob = pickle.dumps(export_payload)
            doc_dir = self._c.settings.storage_root / document.id
            doc_dir.mkdir(parents=True, exist_ok=True)
            (doc_dir / "v3_export.pkl").write_bytes(blob)
            if document.content_hash:
                bucket = self._c.settings.artifact_cache_dir / document.content_hash[:16]
                bucket.mkdir(parents=True, exist_ok=True)
                (bucket / "v3_export.pkl").write_bytes(blob)

            html_src = None
            if manifest:
                html_src = (manifest.get("artifacts") or {}).get("html_postprocessed")
            if not html_src and sample_dir.is_dir():
                mf = sample_dir / "manifest.json"
                if mf.is_file():
                    import json

                    art = json.loads(mf.read_text(encoding="utf-8")).get("artifacts") or {}
                    html_src = art.get("html_postprocessed")
            try:
                if html_src and Path(html_src).is_file():
                    cache = ArtifactCache(self._c.settings.artifact_cache_dir)
                    cache.save_extraction(
                        document=document,
                        requirements=requirements,
                        html_path=Path(html_src),
                        extraction_meta=None,
                        event_bus=self._c.event_bus,
                    )
            except Exception:  # noqa: BLE001
                logger.warning("v3-final 추출 캐시 저장 실패(무시)", exc_info=True)

            await self._pipeline.emit(
                document.id,
                PipelineStage.READY_FOR_REVIEW,
                payload={
                    "requirements": total,
                    "snippet": f"v3-final {strategy} · {total}줄 — {entry.get('label', sample_id)}",
                    "pipeline_steps": steps[-3:],
                    **llm_meta,
                },
            )
        except Exception as e:  # noqa: BLE001
            await self._pipeline.emit_failed(document.id, PipelineStage.READY_FOR_REVIEW, str(e))
            raise

        asyncio.create_task(self._recommend_background(document.id, use_cache=False))
        return document.id

    async def _run_v2(self, document: Document) -> str:
        """prototype/v2 엔진으로 추출 — results_final 산출 파이프라인."""
        import pickle

        try:
            tracker = self._c.llm_tracker(document.id)
            llm_meta = {
                "llm_provider": self._c.settings.llm_provider,
                "llm_model": self._c.active_llm_model(),
            }
            await self._pipeline.emit(document.id, PipelineStage.CONVERTING, payload=llm_meta)

            from prototype.v2.pipeline import run as v2_run
            from app.domain.enums import DocumentMime
            from app.services.pipeline_logger import PipelineLogSession

            tab_mode = self._c.settings.v2_tab_mode
            run_id = (document.content_hash or document.id)[:16]
            log_session = PipelineLogSession(
                self._c.settings.pipeline_logs_dir,
                run_id=run_id,
                source_path=Path(document.src_path),
                source_name=document.source_filename or Path(document.src_path).name,
                content_hash=document.content_hash,
                engine="v2",
            )
            # PDF/HTML은 그대로, HWPX/DOC/DOCX는 앱 컨버터로 HTML 변환 후 V2에 투입
            v2_input = await self._v2_input_path(document, log_session=log_session)
            korean_hwp = document.mime in (DocumentMime.HWP, DocumentMime.HWPX)
            manifest = await asyncio.to_thread(
                v2_run,
                v2_input,
                None,
                mode="llm",
                tab_mode=tab_mode,
                korean_hwp=korean_hwp,
                log_session=log_session,
            )
            if document.content_hash:
                bucket = self._c.settings.artifact_cache_dir / document.content_hash[:16]
                log_session.mirror_to(bucket)
            v2_reqs = manifest.get("_reqs") or []
            overview = manifest.get("_overview")
            await self._pipeline.emit(
                document.id,
                PipelineStage.ATOMIZED,
                payload={"atoms": len(v2_reqs), "strategy": "v2", **llm_meta},
            )

            requirements = [self._v2_to_requirement(document.id, r) for r in v2_reqs]
            await self._c.repo.save_document(document)
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
                        "snippet": f"V2 추출 {i + 1}/{total} · {(req.name or req.detail)[:48]}…",
                    },
                )

            # V2 원본 reqs+overview 저장 (export = results_final 포맷)
            payload = {"reqs": v2_reqs, "overview": overview, "tab_mode": tab_mode}
            blob = pickle.dumps(payload)
            doc_dir = self._c.settings.storage_root / document.id
            doc_dir.mkdir(parents=True, exist_ok=True)
            (doc_dir / "v2_export.pkl").write_bytes(blob)
            if document.content_hash:
                bucket = self._c.settings.artifact_cache_dir / document.content_hash[:16]
                bucket.mkdir(parents=True, exist_ok=True)
                (bucket / "v2_export.pkl").write_bytes(blob)

            # 재오픈 시 재실행(스피너) 방지 — 추출 결과를 아티팩트 캐시에 저장
            try:
                html_src = (manifest.get("artifacts") or {}).get("source_html")
                if html_src and Path(html_src).is_file():
                    cache = ArtifactCache(self._c.settings.artifact_cache_dir)
                    cache.save_extraction(
                        document=document,
                        requirements=requirements,
                        html_path=Path(html_src),
                        extraction_meta=None,
                        event_bus=self._c.event_bus,
                    )
            except Exception:  # noqa: BLE001
                logger.warning("V2 추출 캐시 저장 실패(무시)", exc_info=True)

            await self._pipeline.emit(
                document.id,
                PipelineStage.READY_FOR_REVIEW,
                payload={
                    "requirements": total,
                    "snippet": f"V2 추출 {total}줄 완료 — Excel·검토 가능",
                    "llm_usage": tracker.to_dict(),
                    **llm_meta,
                },
            )
        except Exception as e:  # noqa: BLE001
            await self._pipeline.emit_failed(document.id, PipelineStage.READY_FOR_REVIEW, str(e))
            raise

        asyncio.create_task(self._recommend_background(document.id, use_cache=False))
        return document.id

    @staticmethod
    def _v2_to_requirement(doc_id: str, r) -> Requirement:
        cat = (getattr(r, "tab", "") or "미분류").strip() or "미분류"
        mid = (getattr(r, "mid", "") or "").strip()
        top = (getattr(r, "top", "") or "").strip()
        detail = getattr(r, "detail", "") or ""
        name = top or atom_title(detail) or (detail[:40] if detail else "")
        # V2 gen_* 플래그(원문 추출이 아닌 LLM 생성값)를 export 칼럼 key 로 매핑
        # → Excel writer 가 해당 셀을 주황색으로 표시 (V2 native excel 과 동일 규칙).
        gen_fields: set[str] = set()
        if getattr(r, "gen_rid", False):
            gen_fields.add("code")
        if getattr(r, "gen_top", False) and top:
            gen_fields.add("name")
        if getattr(r, "gen_mid", False) and mid:
            gen_fields.update({"subcategory", "definition"})
        from app.services.native_export import rel_asset_path

        return Requirement(
            id=uuid.uuid4().hex,
            doc_id=doc_id,
            category=cat,
            subcategory=None,
            category_source=CategorySource.DOCUMENT_TABLE,
            subcategory_source=None,
            code=getattr(r, "rid", "") or "",
            name=name,
            definition=mid or None,
            detail=detail or name,
            source_atomic_id=None,
            source_page=getattr(r, "page", None),
            source_section=(getattr(r, "section_path", "") or None),
            source_table_index=getattr(r, "table_id", None),
            source_ref=(getattr(r, "source", "") or None),
            detail_images=[
                rel_asset_path(p) for p in (getattr(r, "detail_images", None) or [])
            ],
            ai_generated_fields=gen_fields,
        )

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
