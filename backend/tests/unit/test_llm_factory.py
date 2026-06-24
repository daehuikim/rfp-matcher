from __future__ import annotations

import pytest

from app.core.config import Settings
from app.llm.factory import active_llm_model, build_llm_client, normalize_llm_provider


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gemma", "gemma"),
        ("Gemma", "gemma"),
        ("gpt-4o", "openai"),
        ("openai", "openai"),
        ("invalid", None),
    ],
)
def test_normalize_llm_provider(raw: str, expected: str | None) -> None:
    assert normalize_llm_provider(raw) == expected


def test_default_provider_is_gemma() -> None:
    s = Settings()
    assert s.llm_provider == "gemma"
    assert active_llm_model(s) == "gemma-4-26B-4aB-it"


def test_build_gemma_client() -> None:
    s = Settings(llm_provider="gemma")
    client = build_llm_client(s)
    assert client.__class__.__name__ == "OpenAIClient"
