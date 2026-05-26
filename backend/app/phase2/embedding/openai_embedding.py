from __future__ import annotations

from openai import AsyncOpenAI

from .base import EmbeddingProvider

# text-embedding-3-large = 3072, -small = 1536
_KNOWN_DIMS = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbedding(EmbeddingProvider):
    """OpenAI 임베딩. **컨테이너 lifespan에서 1회 생성** — 호출마다 재생성 금지."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-large") -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._dim = _KNOWN_DIMS.get(model, 1536)

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in resp.data]
