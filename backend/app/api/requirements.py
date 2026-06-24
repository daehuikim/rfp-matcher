from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.domain.enums import Judgement, PipelineStage
from app.domain.models import HumanJudgement, PipelineEvent, Recommendation, Requirement
from app.services.native_export import enrich_requirements

router = APIRouter(tags=["requirements"])


class RequirementView(BaseModel):
    requirement: Requirement
    recommendation: Recommendation | None
    judgement: HumanJudgement | None


@router.get("/documents/{doc_id}/requirements", response_model=list[RequirementView])
async def list_requirements(doc_id: str, container: ContainerDep) -> list[RequirementView]:
    reqs, recs, judges = await container.repo.snapshot(doc_id)
    doc = container.repo.documents.get(doc_id)
    enriched = enrich_requirements(container.settings, doc_id, doc, reqs)
    return [
        RequirementView(
            requirement=r,
            recommendation=recs.get(r.id),
            judgement=judges.get(r.id),
        )
        for r in enriched
    ]


class JudgementPatch(BaseModel):
    mark: Judgement
    note: str = ""


@router.patch("/requirements/{req_id}/judgement", response_model=HumanJudgement)
async def update_judgement(
    req_id: str,
    patch: JudgementPatch,
    container: ContainerDep,
    x_editor_id: str | None = Header(default=None),
) -> HumanJudgement:
    """
    사람 판정 저장.

    정책:
      - last-write-wins (서버 ts 기준).
      - 성공시 EventBus에 `JUDGEMENT_UPDATED` publish → 같은 doc_id를 SSE 구독중인
        다른 사용자 화면에 즉시 broadcast. 본인 화면은 `X-Editor-Id` 헤더로 echo 식별.
    """
    req = container.repo.requirements.get(req_id)
    if req is None:
        raise HTTPException(404, f"requirement 없음: {req_id}")
    jud = HumanJudgement(requirement_id=req_id, mark=patch.mark, note=patch.note)
    await container.repo.upsert_judgement(jud)
    await container.event_bus.publish(
        PipelineEvent(
            doc_id=req.doc_id,
            stage=PipelineStage.JUDGEMENT_UPDATED,
            payload={
                "requirement_id": req_id,
                "mark": jud.mark.value,
                "note": jud.note,
                "editor_id": x_editor_id or "",
                "ts": jud.updated_at.isoformat(),
            },
        )
    )
    return jud
