from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.domain.models import Recommendation
from app.services.recommendation import RecommendationService

router = APIRouter(tags=["recommendations"])


class RecommendStartResponse(BaseModel):
    doc_id: str
    queued: bool


@router.post("/documents/{doc_id}/recommend", response_model=RecommendStartResponse)
async def start_recommendation(
    doc_id: str,
    background: BackgroundTasks,
    container: ContainerDep,
    force: bool = False,
) -> RecommendStartResponse:
    """AI 검토(매칭) 시작. force=True 면 이미 판정된 요건도 지우고 전량 재평가 —
    사람이 카드 삭제/병합/편집을 마친 뒤 FE의 'AI 검토 시작' 버튼이 쓰는 경로."""
    if doc_id not in container.repo.documents:
        raise HTTPException(404, f"document 없음: {doc_id}")
    reqs = await container.repo.list_requirements(doc_id)
    if not reqs:
        raise HTTPException(409, "추출된 요구사항 없음 — 먼저 업로드/추출 완료 필요")
    if force:
        await container.repo.clear_recommendations(doc_id)
    svc = RecommendationService(container)

    async def _runner() -> None:
        await svc.recommend_document(doc_id)

    background.add_task(_runner)
    return RecommendStartResponse(doc_id=doc_id, queued=True)


@router.get("/requirements/{req_id}/recommendation", response_model=Recommendation | None)
async def get_recommendation(req_id: str, container: ContainerDep) -> Recommendation | None:
    return await container.repo.get_recommendation(req_id)
