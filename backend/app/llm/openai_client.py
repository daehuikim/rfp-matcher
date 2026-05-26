from __future__ import annotations

import json
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from .base import AsyncLlmClient, Message

T = TypeVar("T", bound=BaseModel)


class OpenAIClient(AsyncLlmClient):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def chat(self, messages: list[Message], **kwargs: Any) -> str:
        resp = await self._client.chat.completions.create(
            model=kwargs.get("model", self._model),
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=kwargs.get("temperature", 0.0),
        )
        return resp.choices[0].message.content or ""

    async def structured_output(
        self,
        messages: list[Message],
        schema: type[T],
        **kwargs: Any,
    ) -> T:
        resp = await self._client.chat.completions.create(
            model=kwargs.get("model", self._model),
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=kwargs.get("temperature", 0.0),
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        return schema.model_validate(json.loads(raw))
