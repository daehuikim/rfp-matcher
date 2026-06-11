from __future__ import annotations

import logging
from typing import Literal

from app.core.config import Settings
from app.llm.base import AsyncLlmClient
from app.llm.fake_client import FakeLlmClient

logger = logging.getLogger(__name__)

LlmChoiceId = Literal["gemma", "gpt-4o"]

LLM_OPTIONS: list[dict[str, str]] = [
    {
        "id": "gemma",
        "label": "Gemma 4",
        "provider": "gemma",
        "model": "gemma-4-26B-4aB-it",
    },
    {
        "id": "gpt-4o",
        "label": "GPT-4o",
        "provider": "openai",
        "model": "gpt-4o",
    },
]

_VALID_PROVIDERS = frozenset({"gemma", "openai", "anthropic", "fake"})


def normalize_llm_provider(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v in {"gemma", "gemma-4", "gemma-4-26b-4ab-it"}:
        return "gemma"
    if v in {"gpt-4o", "openai", "gpt4o"}:
        return "openai"
    if v in _VALID_PROVIDERS:
        return v
    return None


def active_llm_model(settings: Settings) -> str:
    if settings.llm_provider == "openai":
        return settings.llm_model_openai
    if settings.llm_provider == "anthropic":
        return settings.llm_model_anthropic
    if settings.llm_provider == "gemma":
        return settings.llm_model_gemma
    return "fake"


def build_llm_client(settings: Settings) -> AsyncLlmClient:
    if settings.llm_provider == "fake":
        return FakeLlmClient()
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            logger.warning("OPENAI_API_KEY 미설정 — FakeLlmClient로 폴백")
            return FakeLlmClient()
        try:
            from app.llm.openai_client import OpenAIClient
        except ImportError as e:
            logger.warning("openai SDK 미설치 (%s) — FakeLlmClient로 폴백", e)
            return FakeLlmClient()
        return OpenAIClient(settings.openai_api_key, settings.llm_model_openai)
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            logger.warning("ANTHROPIC_API_KEY 미설정 — FakeLlmClient로 폴백")
            return FakeLlmClient()
        try:
            from app.llm.anthropic_client import AnthropicClient
        except ImportError as e:
            logger.warning("anthropic SDK 미설치 (%s) — FakeLlmClient로 폴백", e)
            return FakeLlmClient()
        return AnthropicClient(settings.anthropic_api_key, settings.llm_model_anthropic)
    if settings.llm_provider == "gemma":
        try:
            from app.llm.openai_client import OpenAIClient
        except ImportError as e:
            logger.warning("openai SDK 미설치 (%s) — FakeLlmClient로 폴백", e)
            return FakeLlmClient()
        return OpenAIClient(
            api_key="not-needed",
            model=settings.llm_model_gemma,
            base_url=settings.gemma_base_url,
            verify_ssl=settings.gemma_verify_ssl,
        )
    raise ValueError(f"unknown llm_provider: {settings.llm_provider}")


def apply_llm_provider(container, provider: str | None) -> None:
    """런타임 LLM provider 교체 — 업로드·설정 API 공용."""
    normalized = normalize_llm_provider(provider)
    if normalized is None:
        return
    container.settings.llm_provider = normalized  # type: ignore[assignment]
    container.llm = build_llm_client(container.settings)
