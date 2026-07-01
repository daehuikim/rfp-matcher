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

    async def update_requirement(self, req_id: str, fields: dict) -> Requirement | None:
        """한 요구사항의 칸 편집(요구사항명/계위/상세내용/ID 등) — FE 인라인 편집."""
        async with self._lock:
            r = self.requirements.get(req_id)
            if r is None:
                return None
            upd = r.model_copy(update=fields)
            self.requirements[req_id] = upd
            return upd

    async def delete_requirement(self, doc_id: str, req_id: str) -> bool:
        """행 삭제 — 목록·요건·추천·판정에서 제거(ID 재정렬은 상위에서 renumber 호출)."""
        async with self._lock:
            ids = self.requirements_by_doc.get(doc_id, [])
            if req_id not in ids:
                return False
            ids.remove(req_id)
            self.requirements.pop(req_id, None)
            self.recommendations.pop(req_id, None)
            self.judgements.pop(req_id, None)
            return True

    async def reorder_requirements(self, doc_id: str, ordered_ids: list[str]) -> None:
        async with self._lock:
            self.requirements_by_doc[doc_id] = [i for i in ordered_ids if i in self.requirements]

    async def insert_requirements_after(self, doc_id: str, after_id: str, reqs: list[Requirement]) -> None:
        """분해(split) — 원본 행 바로 뒤에 새 행들을 삽입."""
        async with self._lock:
            for r in reqs:
                self.requirements[r.id] = r
            ids = self.requirements_by_doc.setdefault(doc_id, [])
            pos = ids.index(after_id) + 1 if after_id in ids else len(ids)
            ids[pos:pos] = [r.id for r in reqs]

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
