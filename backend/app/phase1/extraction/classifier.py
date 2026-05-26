from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.domain.models import AtomicRow

logger = logging.getLogger(__name__)


class Classifier(ABC):
    @abstractmethod
    async def classify(self, atoms: list[AtomicRow]) -> list[str]:
        """입력 atoms 순서를 유지하며 각 항목의 raw category 라벨을 반환."""


class PassThroughClassifier(Classifier):
    """조견표 왼쪽 셀(`category_raw`)을 공백만 정규화해 반환 — canonicalize는 별도 스텝."""

    async def classify(self, atoms: list[AtomicRow]) -> list[str]:
        return [_normalize_raw(a.category_raw) for a in atoms]


def select_classifier(
    atoms: list[AtomicRow],
    llm: object | None = None,
    *,
    coverage_floor: float = 0.6,
    diversity_floor: int = 1,
) -> Classifier:
    if llm is not None:
        logger.debug("select_classifier: llm 인자는 사용하지 않음 (PassThrough 고정)")
    if atoms:
        raws = [_normalize_raw(a.category_raw) for a in atoms]
        filled = [r for r in raws if r]
        logger.info(
            "classifier: PassThrough coverage=%.2f raw_distinct=%d (n=%d)",
            len(filled) / len(atoms),
            len(set(filled)),
            len(atoms),
        )
    return PassThroughClassifier()


def _normalize_raw(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.split())
