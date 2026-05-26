from __future__ import annotations

import asyncio
import os

import pytest

# 테스트 환경에서는 PDF 컨버터를 무조건 pdfplumber로 강제 — Docling이 HF 모델을 받지 않게.
# (production 디폴트는 docling.)
os.environ.setdefault("PDF_CONVERTER", "pdfplumber")

from app.core import config  # noqa: E402

config.get_settings.cache_clear()

from app.llm.fake_client import FakeLlmClient  # noqa: E402
from app.services.event_bus import EventBus  # noqa: E402


@pytest.fixture
def fake_llm() -> FakeLlmClient:
    return FakeLlmClient()


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
