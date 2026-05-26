from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from .base import AsyncLlmClient, Message

T = TypeVar("T", bound=BaseModel)


class FakeLlmClient(AsyncLlmClient):
    """
    단위테스트용 결정적 더블.

    - `chat_responses`: 큐 형태로 미리 넣어둔 응답을 차례대로 반환.
    - `structured_handler`: 스키마별 정답 모델을 만들어주는 콜백.
    """

    def __init__(
        self,
        chat_responses: list[str] | None = None,
        structured_handler: Callable[[type[BaseModel], list[Message]], BaseModel] | None = None,
    ) -> None:
        self._chat = list(chat_responses or [])
        self._structured = structured_handler
        self.calls: list[tuple[str, list[Message], dict[str, Any]]] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> str:
        self.calls.append(("chat", messages, kwargs))
        if not self._chat:
            return ""
        return self._chat.pop(0)

    async def structured_output(
        self,
        messages: list[Message],
        schema: type[T],
        **kwargs: Any,
    ) -> T:
        self.calls.append(("structured_output", messages, kwargs))
        if self._structured is None:
            raise RuntimeError("FakeLlmClient: structured_handler 미설정")
        result = self._structured(schema, messages)
        if not isinstance(result, schema):
            raise TypeError(f"structured_handler가 {schema} 대신 {type(result)} 반환")
        return result
