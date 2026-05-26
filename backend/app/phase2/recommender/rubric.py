from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, Field

from app.domain.enums import Judgement


@dataclass(frozen=True)
class RubricDimension:
    key: str
    label: str
    description: str  # LLM에 줄 평가 가이드


# 5개 차원 — 도메인 전문가가 수정.
DIMENSIONS: list[RubricDimension] = [
    RubricDimension(
        "기술적합도",
        "기술 적합도",
        "요구사항이 우리 솔루션의 핵심 기능으로 직접 커버되는지 (1점=완전 일치, 5점=관련 없음).",
    ),
    RubricDimension(
        "데이터요건",
        "데이터 요건",
        "요구사항이 요구하는 데이터(양·유형·라벨)를 확보 가능한지 (1=쉽게 확보, 5=확보 불가).",
    ),
    RubricDimension(
        "컴플라이언스",
        "컴플라이언스",
        "개인정보·금융·의료 등 규제 요건과 충돌하지 않는지 (1=문제없음, 5=재설계 필요).",
    ),
    RubricDimension(
        "레퍼런스",
        "레퍼런스",
        "유사 프로젝트 레퍼런스가 있는지 (1=다수, 5=무).",
    ),
    RubricDimension(
        "컨소시엄",
        "컨소시엄 필요성",
        "외부 도급/컨소시엄이 필요한지 (1=단독 가능, 5=반드시 컨소시엄).",
    ),
]


class RubricScores(BaseModel):
    기술적합도: float = Field(ge=1, le=5)
    데이터요건: float = Field(ge=1, le=5)
    컴플라이언스: float = Field(ge=1, le=5)
    레퍼런스: float = Field(ge=1, le=5)
    컨소시엄: float = Field(ge=1, le=5)

    THRESHOLD_RISK_PARTIAL: ClassVar[float] = 2.2
    THRESHOLD_RISK_NO: ClassVar[float] = 3.5

    def average(self) -> float:
        return (
            sum(
                [
                    self.기술적합도,
                    self.데이터요건,
                    self.컴플라이언스,
                    self.레퍼런스,
                    self.컨소시엄,
                ]
            )
            / 5.0
        )

    def to_judgement(self) -> Judgement:
        """평균 점수 → O/△/X. 보수적 기본값."""
        avg = self.average()
        if avg <= self.THRESHOLD_RISK_PARTIAL:
            return Judgement.YES
        if avg <= self.THRESHOLD_RISK_NO:
            return Judgement.PARTIAL
        return Judgement.NO

    def as_dict(self) -> dict[str, float]:
        return {
            "기술적합도": self.기술적합도,
            "데이터요건": self.데이터요건,
            "컴플라이언스": self.컴플라이언스,
            "레퍼런스": self.레퍼런스,
            "컨소시엄": self.컨소시엄,
        }
