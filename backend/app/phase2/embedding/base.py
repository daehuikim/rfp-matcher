from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """
    텍스트 → 벡터 변환 추상.

    필수: 인스턴스는 **앱 lifespan 동안 1회 생성**되어 모든 호출이 같은 가중치를 재사용해야 한다.
    호출자는 자신만의 모델을 따로 들고 다니지 않는다.
    """

    @property
    @abstractmethod
    def dim(self) -> int: ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
