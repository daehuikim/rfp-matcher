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


# ── FE 편집 기능(병합/삭제/편집) — repo 요건을 직접 변경, export(고정칼럼)에 즉시 반영 ──
import re as _re


def _renumber_codes(reqs: list[Requirement]) -> dict[str, str]:
    """탭(category)별로 ID 접두사 유지 + 001,002… 재정렬. 반환: {req_id: 새 code}."""
    by_tab: dict[str, list[Requirement]] = {}
    for r in reqs:
        by_tab.setdefault(r.category or "요구사항", []).append(r)
    out: dict[str, str] = {}
    for group in by_tab.values():
        first = group[0].code or ""
        m = _re.match(r"^(.*?)([-_])?(\d+)\s*$", first)
        prefix = (m.group(1) if m and m.group(1) else (first or group[0].category or "REQ"))
        sep = (m.group(2) if m and m.group(2) else "-")
        for i, r in enumerate(group, 1):
            out[r.id] = f"{prefix}{sep}{i:03d}"
    return out


async def _apply_renumber(container, doc_id: str) -> None:
    reqs = await container.repo.list_requirements(doc_id)
    for rid, code in _renumber_codes(reqs).items():
        await container.repo.update_requirement(rid, {"code": code})


class RequirementEditPatch(BaseModel):
    name: str | None = None
    definition: str | None = None   # 계위
    detail: str | None = None
    code: str | None = None         # 요구사항 ID
    category: str | None = None     # 탭


@router.patch("/requirements/{req_id}", response_model=Requirement)
async def edit_requirement(
    req_id: str, patch: RequirementEditPatch, container: ContainerDep,
    x_editor_id: str | None = Header(default=None),
) -> Requirement:
    """편집 — 어떤 칸(요구사항명/계위/상세내용/ID/탭)이든 인라인 수정. 동시편집 즉시 broadcast."""
    fields = {k: v for k, v in patch.model_dump().items() if v is not None}
    upd = await container.repo.update_requirement(req_id, fields)
    if upd is None:
        raise HTTPException(404, f"requirement 없음: {req_id}")
    await container.event_bus.publish(PipelineEvent(
        doc_id=upd.doc_id, stage=PipelineStage.REQUIREMENT_EDITED,
        payload={"requirement_id": req_id, "fields": fields, "editor_id": x_editor_id or ""},
    ))
    return upd


@router.delete("/documents/{doc_id}/requirements/{req_id}", response_model=list[RequirementView])
async def delete_requirement(doc_id: str, req_id: str, container: ContainerDep) -> list[RequirementView]:
    """삭제 — 행 제거 후 탭 내 ID 재정렬(align). 갱신된 목록 반환."""
    ok = await container.repo.delete_requirement(doc_id, req_id)
    if not ok:
        raise HTTPException(404, f"requirement 없음: {req_id}")
    await _apply_renumber(container, doc_id)
    await container.event_bus.publish(PipelineEvent(
        doc_id=doc_id, stage=PipelineStage.REQUIREMENTS_CHANGED, payload={"op": "delete"}))
    return await list_requirements(doc_id, container)


class MergeBody(BaseModel):
    with_id: str   # 아랫줄 id — 윗줄(req_id)에 병합


@router.post("/documents/{doc_id}/requirements/{req_id}/merge", response_model=list[RequirementView])
async def merge_requirements(doc_id: str, req_id: str, body: MergeBody, container: ContainerDep) -> list[RequirementView]:
    """병합 — 윗줄(req_id) 기준으로 아랫줄(with_id)의 상세내용을 줄바꿈 결합, 아랫줄 삭제.

    ID·요구사항명·계위는 윗줄 유지, 상세내용만 '윗줄\\n아랫줄'. 이후 ID 재정렬.
    """
    top = container.repo.requirements.get(req_id)
    bot = container.repo.requirements.get(body.with_id)
    if top is None or bot is None:
        raise HTTPException(404, "병합 대상 requirement 없음")
    merged_detail = (top.detail or "").rstrip() + "\n" + (bot.detail or "").strip()
    await container.repo.update_requirement(req_id, {"detail": merged_detail})
    await container.repo.delete_requirement(doc_id, body.with_id)
    await _apply_renumber(container, doc_id)
    await container.event_bus.publish(PipelineEvent(
        doc_id=doc_id, stage=PipelineStage.REQUIREMENTS_CHANGED, payload={"op": "merge"}))
    return await list_requirements(doc_id, container)
