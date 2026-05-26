from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from app.core.container import Container


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("Container 미초기화 — lifespan 설정 확인")
    return cast(Container, container)


ContainerDep = Annotated[Container, Depends(get_container)]
