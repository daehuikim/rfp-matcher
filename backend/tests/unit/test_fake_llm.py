from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.llm.base import Message
from app.llm.fake_client import FakeLlmClient


class _Echo(BaseModel):
    text: str


@pytest.mark.asyncio
async def test_chat_pops_queued_responses_in_order() -> None:
    client = FakeLlmClient(chat_responses=["a", "b"])
    assert await client.chat([Message(role="user", content="?")]) == "a"
    assert await client.chat([Message(role="user", content="?")]) == "b"
    assert await client.chat([Message(role="user", content="?")]) == ""


@pytest.mark.asyncio
async def test_structured_output_uses_handler() -> None:
    def handler(schema: type[BaseModel], messages: list[Message]) -> BaseModel:
        return schema(text=messages[-1].content.upper())

    client = FakeLlmClient(structured_handler=handler)
    out = await client.structured_output([Message(role="user", content="hi")], _Echo)
    assert isinstance(out, _Echo)
    assert out.text == "HI"


@pytest.mark.asyncio
async def test_structured_output_without_handler_raises() -> None:
    client = FakeLlmClient()
    with pytest.raises(RuntimeError):
        await client.structured_output([Message(role="user", content="hi")], _Echo)
