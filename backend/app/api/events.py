from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.api.deps import ContainerDep

router = APIRouter(tags=["events"])


@router.get("/documents/{doc_id}/events")
async def stream_events(doc_id: str, container: ContainerDep) -> EventSourceResponse:
    async def gen() -> AsyncIterator[dict[str, str]]:
        async for ev in container.event_bus.subscribe(doc_id):
            yield {
                "event": ev.stage.value,
                "data": json.dumps(ev.model_dump(mode="json"), ensure_ascii=False),
            }

    return EventSourceResponse(gen())
