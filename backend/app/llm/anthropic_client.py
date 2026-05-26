from __future__ import annotations

import json
from typing import Any, TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from .base import AsyncLlmClient, Message

T = TypeVar("T", bound=BaseModel)


class AnthropicClient(AsyncLlmClient):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    @staticmethod
    def _split(messages: list[Message]) -> tuple[str, list[dict[str, str]]]:
        system_parts = [m.content for m in messages if m.role == "system"]
        rest = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        return ("\n\n".join(system_parts), rest)

    async def chat(self, messages: list[Message], **kwargs: Any) -> str:
        system, rest = self._split(messages)
        resp = await self._client.messages.create(
            model=kwargs.get("model", self._model),
            max_tokens=kwargs.get("max_tokens", 4096),
            temperature=kwargs.get("temperature", 0.0),
            system=system,
            messages=rest,
        )
        # Anthropic SDK returns a list of content blocks
        parts = [b.text for b in resp.content if getattr(b, "text", None)]
        return "".join(parts)

    async def structured_output(
        self,
        messages: list[Message],
        schema: type[T],
        **kwargs: Any,
    ) -> T:
        guard = Message(
            role="system",
            content=(
                "Return ONLY a single JSON object that matches the following schema. "
                f"No prose. Schema:\n{json.dumps(schema.model_json_schema())}"
            ),
        )
        raw = await self.chat([guard, *messages], **kwargs)
        # strip code fences if any
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        return schema.model_validate(json.loads(cleaned))
