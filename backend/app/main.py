from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# HuggingFace 캐시 위치를 라이브러리 import 이전에 고정한다 — Docling/sentence-transformers 등이
# 호출되더라도 재시작마다 새로 다운로드하지 않고 프로젝트 내부 캐시(data/.cache/huggingface)를
# 재사용. Settings 로드보다 먼저 환경변수가 박혀야 import 시점에 반영된다.
from app.core.config import get_settings  # noqa: E402

_s = get_settings()
_s.hf_cache_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(_s.hf_cache_dir))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(_s.hf_cache_dir))
os.environ.setdefault("TRANSFORMERS_CACHE", str(_s.hf_cache_dir))

from fastapi import FastAPI  # noqa: E402

from app.api import documents, events, exports, health, recommendations, requirements  # noqa: E402
from app.core.container import build_container, teardown_container  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    container = await build_container()
    app.state.container = container
    try:
        yield
    finally:
        await teardown_container(container)


def create_app() -> FastAPI:
    app = FastAPI(title="rfp-matcher", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(documents.router)
    app.include_router(requirements.router)
    app.include_router(recommendations.router)
    app.include_router(exports.router)
    return app


app = create_app()
