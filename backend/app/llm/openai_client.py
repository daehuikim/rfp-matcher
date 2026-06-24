from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from .base import AsyncLlmClient, Message

if TYPE_CHECKING:
    from .usage import LlmUsageTracker

T = TypeVar("T", bound=BaseModel)


class OpenAIClient(AsyncLlmClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        client_kwargs: dict[str, object] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        if not verify_ssl:
            import httpx

            client_kwargs["http_client"] = httpx.AsyncClient(verify=False)
        self._client = AsyncOpenAI(**client_kwargs)
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
        purpose = kwargs.pop("purpose", "structured")
        tracker: LlmUsageTracker | None = kwargs.pop("tracker", None)
        create_kwargs: dict[str, Any] = {
            "model": kwargs.get("model", self._model),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "seed": kwargs.get("seed", 0),
        }
        if kwargs.get("max_tokens") is not None:
            create_kwargs["max_tokens"] = kwargs["max_tokens"]
        resp = await self._client.chat.completions.create(**create_kwargs)
        if tracker and resp.usage:
            tracker.record(
                purpose=purpose,
                messages=messages,
                model=resp.model or self._model,
                input_tokens=resp.usage.prompt_tokens or 0,
                output_tokens=resp.usage.completion_tokens or 0,
            )
        raw = resp.choices[0].message.content or "{}"
        return schema.model_validate(json.loads(raw))
