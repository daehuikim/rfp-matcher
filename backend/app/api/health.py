from __future__ import annotations

import os

from fastapi import APIRouter

from app.api.deps import ContainerDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(container: ContainerDep) -> dict[str, str]:
    s = container.settings
    return {
        "status": "ok",
        "app": s.app_name,
        "llm_provider": s.llm_provider,
        "pdf_converter": s.pdf_converter,
        "pdf_converter_type": type(container.pdf_converter).__name__,
        "catalog_retriever": "bm25s",
        "catalog_entries": str(await container.catalog_retriever.count()),
        "hf_home": os.environ.get("HF_HOME", "(unset)"),
    }
