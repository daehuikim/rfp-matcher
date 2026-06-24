from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.domain.models import Document, HumanJudgement, Recommendation, Requirement


@dataclass
class InMemoryRepo:
    """
    MVP용 in-process 저장소. 단일 인스턴스(컨테이너 lifespan)에서 공유,
    동시 쓰기는 asyncio.Lock으로 직렬화한다.
    """

    documents: dict[str, Document] = field(default_factory=dict)
    requirements: dict[str, Requirement] = field(default_factory=dict)
    requirements_by_doc: dict[str, list[str]] = field(default_factory=dict)
    judgements: dict[str, HumanJudgement] = field(default_factory=dict)
    recommendations: dict[str, Recommendation] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def save_document(self, doc: Document) -> None:
        async with self._lock:
            self.documents[doc.id] = doc
            self.requirements_by_doc.setdefault(doc.id, [])

    async def save_requirements(self, doc_id: str, reqs: list[Requirement]) -> None:
        async with self._lock:
            for r in reqs:
                self.requirements[r.id] = r
            self.requirements_by_doc[doc_id] = [r.id for r in reqs]

    async def append_requirement(self, doc_id: str, req: Requirement) -> None:
        """조견표 한 줄씩 저장 — 프론트가 추출 진행 중에도 목록을 갱신할 수 있게."""
        async with self._lock:
            self.requirements[req.id] = req
            ids = self.requirements_by_doc.setdefault(doc_id, [])
            if req.id not in ids:
                ids.append(req.id)

    async def list_requirements(self, doc_id: str) -> list[Requirement]:
        async with self._lock:
            ids = self.requirements_by_doc.get(doc_id, [])
            return [self.requirements[i] for i in ids if i in self.requirements]

    async def upsert_judgement(self, jud: HumanJudgement) -> None:
        async with self._lock:
            self.judgements[jud.requirement_id] = jud

    async def get_judgement(self, req_id: str) -> HumanJudgement | None:
        async with self._lock:
            return self.judgements.get(req_id)

    async def upsert_recommendation(self, rec: Recommendation) -> None:
        async with self._lock:
            self.recommendations[rec.requirement_id] = rec

    async def get_recommendation(self, req_id: str) -> Recommendation | None:
        async with self._lock:
            return self.recommendations.get(req_id)

    async def snapshot(self, doc_id: str) -> tuple[
        list[Requirement],
        dict[str, Recommendation],
        dict[str, HumanJudgement],
    ]:
        async with self._lock:
            ids = self.requirements_by_doc.get(doc_id, [])
            reqs = [self.requirements[i] for i in ids if i in self.requirements]
            recs = {i: self.recommendations[i] for i in ids if i in self.recommendations}
            jdg = {i: self.judgements[i] for i in ids if i in self.judgements}
            return reqs, recs, jdg

    async def clear_extraction_data(self, doc_id: str) -> None:
        """디스크 캐시 삭제·재추출 시 in-memory 요건·AI·판정 제거."""
        async with self._lock:
            ids = self.requirements_by_doc.pop(doc_id, [])
            for req_id in ids:
                self.requirements.pop(req_id, None)
                self.recommendations.pop(req_id, None)
                self.judgements.pop(req_id, None)

    async def clear_all(self) -> None:
        """모든 문서·요건·판정·추천 제거 — 워크스페이스 초기화."""
        async with self._lock:
            self.documents.clear()
            self.requirements.clear()
            self.requirements_by_doc.clear()
            self.judgements.clear()
            self.recommendations.clear()
