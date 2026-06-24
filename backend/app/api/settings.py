from __future__ import annotations

from pydantic import BaseModel, Field

from app.api.deps import ContainerDep
from app.llm.factory import LLM_OPTIONS, normalize_llm_provider
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/settings", tags=["settings"])


class LlmSettingsResponse(BaseModel):
    provider: str
    model: str
    options: list[dict[str, str]]


class LlmSettingsPatch(BaseModel):
    provider: str = Field(description="gemma | openai (gpt-4o)")


@router.get("/llm", response_model=LlmSettingsResponse)
async def get_llm_settings(container: ContainerDep) -> LlmSettingsResponse:
    return LlmSettingsResponse(
        provider=container.settings.llm_provider,
        model=container.active_llm_model(),
        options=LLM_OPTIONS,
    )


@router.patch("/llm", response_model=LlmSettingsResponse)
async def patch_llm_settings(
    body: LlmSettingsPatch,
    container: ContainerDep,
) -> LlmSettingsResponse:
    normalized = normalize_llm_provider(body.provider)
    if normalized not in {"gemma", "openai"}:
        raise HTTPException(400, "provider는 gemma 또는 openai(gpt-4o)만 지원합니다")
    container.set_llm_provider(normalized)
    return LlmSettingsResponse(
        provider=container.settings.llm_provider,
        model=container.active_llm_model(),
        options=LLM_OPTIONS,
    )
