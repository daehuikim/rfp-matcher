from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from app.core.container import Container
from app.domain.enums import DocumentMime, PipelineStage
from app.domain.models import AtomicRow, Document, Requirement
from app.phase1.converters.registry import select_converter
from app.phase1.extraction.classifier import select_classifier
from app.phase1.extraction.row_atomizer import ParagraphAtomizer, RowAtomizer
from app.phase1.extraction.table_locator import TableLocator
from app.phase1.loaders.base import select_loader
from app.phase1.extraction.parsing import atom_title
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
        await self._c.repo.save_document(document)
        await self._pipeline.emit(document.id, PipelineStage.UPLOADED)
        return document

    async def run(self, document: Document) -> str:
        """이미 등록된 Document에 대해 변환→탐지→분해→Requirement 저장 파이프라인 수행."""

        try:
            # 1) HTML 변환 — 컨버터는 lifespan singleton에서 빌려 쓴다 (재생성 금지)
            await self._pipeline.emit(document.id, PipelineStage.CONVERTING)
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
            locator = TableLocator(self._c.llm)
            refs = await locator.locate(document.id, html_doc.html_path)
            await self._pipeline.emit(
                document.id, PipelineStage.LOCATED, payload={"tables": len(refs)}
            )

            # 3) atomic 분해 (표 없으면 단락 폴백) — 표별 병렬
            await self._pipeline.emit(document.id, PipelineStage.ATOMIZING)
            atoms: list[AtomicRow] = []
            if refs:
                atomizer = RowAtomizer(self._c.llm)
                parts = await asyncio.gather(
                    *[
                        atomizer.atomize(document.id, html_doc.html_path, ref)
                        for ref in refs
                    ]
                )
                for part in parts:
                    atoms.extend(part)
            else:
                logger.info("표 없음 — ParagraphAtomizer 폴백")
                atoms = await ParagraphAtomizer().atomize(document.id, html_doc.html_path)
            await self._pipeline.emit(
                document.id, PipelineStage.ATOMIZED, payload={"atoms": len(atoms)}
            )

            # 4) 분류 — 명시 분류가 충분하면 PassThrough, 아니면 LLM 어댑티브 스키마
            await self._pipeline.emit(document.id, PipelineStage.CLASSIFYING)
            classifier = select_classifier(atoms, self._c.llm)
            categories = await classifier.classify(atoms)
            await self._pipeline.emit(
                document.id,
                PipelineStage.CLASSIFIED,
                payload={
                    "classifier": type(classifier).__name__,
                    "distinct_categories": len(set(categories)),
                },
            )

            # 5) Requirement로 변환 — 한 줄씩 저장·이벤트 (UI에서 조견표가 순차 표시)
            requirements = self._to_requirements(document.id, atoms, categories)
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
                payload={"requirements": total, "snippet": f"조견표 {total}줄 추출 완료 — Excel·검토 가능"},
            )
        except Exception as e:  # noqa: BLE001
            await self._pipeline.emit_failed(document.id, PipelineStage.READY_FOR_REVIEW, str(e))
            raise

        # 추출이 끝나면 곧바로 AI 추천을 백그라운드로 시작. 사용자는 이미 조견표를 보고
        # Excel로 내려받거나 사람 판정을 시작할 수 있고, 추천은 카드별로 도착하는 대로 갱신된다.
        asyncio.create_task(self._recommend_background(document.id))

        return document.id

    async def _recommend_background(self, doc_id: str) -> None:
        from app.services.recommendation import RecommendationService

        try:
            await RecommendationService(self._c).recommend_document(doc_id)
        except Exception:
            logger.exception("백그라운드 추천 실패 doc=%s", doc_id)

    @staticmethod
    def _to_requirements(
        doc_id: str, atoms: list[AtomicRow], categories: list[str]
    ) -> list[Requirement]:
        """
        atomic 단위 그대로 Requirement로 매핑.

        category는 Classifier가 반환한 라벨을 사용,
        명칭은 본문 첫 30자, 코드는 분류접두 + 일련번호.
        """
        if len(categories) != len(atoms):
            raise ValueError(f"categories({len(categories)})와 atoms({len(atoms)}) 길이 불일치")
        out: list[Requirement] = []
        counts: dict[str, int] = {}
        for a, cat in zip(atoms, categories, strict=True):
            cat = cat or "기타"
            counts[cat] = counts.get(cat, 0) + 1
            code = f"{_code_prefix(cat)}-{counts[cat]:03d}"
            name = atom_title(a.text)
            out.append(
                Requirement(
                    id=uuid.uuid4().hex,
                    doc_id=doc_id,
                    category=cat,
                    code=code,
                    name=name,
                    detail=a.text,
                    source_atomic_id=None,
                )
            )
        return out


def _code_prefix(category: str) -> str:
    # 첫 4글자(영문자/숫자만) — 분류 없으면 "REQ"
    cleaned = "".join(ch for ch in category if ch.isalnum())
    return cleaned[:4].upper() or "REQ"
