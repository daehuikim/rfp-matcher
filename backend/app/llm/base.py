from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Message(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str


class AsyncLlmClient(ABC):
    """
    LLM 클라이언트 추상.

    동기 래퍼는 두지 않는다 — async 호출만 허용해 병렬화·이벤트 기반 step을 강제한다.
    여러 항목 동시 처리는 `asyncio.gather` + `asyncio.Semaphore`로 호출 측에서 제어.
    """

    @abstractmethod
    async def chat(self, messages: list[Message], **kwargs: Any) -> str: ...

    @abstractmethod
    async def structured_output(
        self,
        messages: list[Message],
        schema: type[T],
        **kwargs: Any,
    ) -> T: ...
