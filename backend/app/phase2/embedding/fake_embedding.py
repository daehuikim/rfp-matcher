from __future__ import annotations

import hashlib
import math

from .base import EmbeddingProvider


class FakeEmbedding(EmbeddingProvider):
    """
    결정적 해시 기반 임베딩 — 테스트 전용. SHA256 해시를 dim차원 벡터로 펴서 정규화.

    같은 텍스트는 항상 같은 벡터가 나오고, 다른 텍스트는 거의 직교에 가까운 분포를 가진다.
    """

    def __init__(self, dim: int = 64) -> None:
        if dim <= 0 or dim > 256:
            raise ValueError("dim은 1~256 사이여야 함")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # bytes를 dim개로 매핑 — 필요시 여러 해시 반복
        buf = bytearray()
        while len(buf) < self._dim:
            buf.extend(hashlib.sha256(buf + h).digest())
        vec = [(b / 255.0) - 0.5 for b in buf[: self._dim]]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]
