"""파이프라인 내 다중 LLM 호출 — 스레드별 event loop 재사용 (FastAPI to_thread 안전)."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_tls = threading.local()


def _get_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_tls, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _tls.loop = loop
    return loop


def run_coro(coro) -> T:
    return _get_loop().run_until_complete(coro)


def reset_loop() -> None:
    """스레드 로컬 루프 정리 — running 중이면 닫지 않음 (uvicorn 루프 오염 방지)."""
    loop = getattr(_tls, "loop", None)
    if loop is None or loop.is_closed():
        _tls.loop = None
        return
    if loop.is_running():
        logger.debug("reset_loop: skip — loop still running")
        return
    try:
        loop.run_until_complete(asyncio.sleep(0))
    except Exception:
        pass
    try:
        loop.close()
    except RuntimeError as e:
        logger.debug("reset_loop: close skipped (%s)", e)
    _tls.loop = None
