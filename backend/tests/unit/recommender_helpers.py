from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from pydantic import BaseModel

from app.llm.base import Message
from app.phase2.recommender.rubric import RubricScores


def _extract_requirement_ids(messages: list[Message]) -> list[str]:
    text = messages[0].content if messages else ""
    return re.findall(r"requirement_id=([^\s\n]+)", text)


def batch_yes_handler(schema: type[BaseModel], msgs: list[Message]) -> BaseModel:
    """배치 _BatchVerdict 또는 단건 스키마 모두 처리."""
    fields = schema.model_fields
    if "results" in fields:
        ids = _extract_requirement_ids(msgs) or ["r1"]
        return schema.model_validate(
            {
                "results": [
                    {
                        "requirement_id": rid,
                        "verified_ids": [],
                        "excluded_notes": [],
                        "rubric": {
                            "기술적합도": 1,
                            "데이터요건": 1,
                            "컴플라이언스": 2,
                            "레퍼런스": 2,
                            "컨소시엄": 1,
                        },
                        "reason": "기존 카탈로그로 충분히 커버",
                        "missing_tech": [],
                        "consortium_need": None,
                    }
                    for rid in ids
                ]
            }
        )
    return schema.model_validate(
        {
            "rubric": {
                "기술적합度": 1,
            }
        }
    )


def batch_no_handler(schema: type[BaseModel], msgs: list[Message]) -> BaseModel:
    fields = schema.model_fields
    if "results" in fields:
        ids = _extract_requirement_ids(msgs) or ["r2"]
        return schema.model_validate(
            {
                "results": [
                    {
                        "requirement_id": rid,
                        "verified_ids": [],
                        "excluded_notes": [],
                        "rubric": {
                            "기술적합도": 5,
                            "데이터요건": 4,
                            "컴플라이언스": 5,
                            "레퍼런스": 4,
                            "컨소시엄": 3,
                        },
                        "reason": "도메인 특화 데이터·규제 준수 어려움",
                        "missing_tech": ["의료영상 라벨링", "FDA 인증 모듈"],
                        "consortium_need": "의료 AI 전문 SI",
                    }
                    for rid in ids
                ]
            }
        )
    return schema.model_validate(
        {
            "rubric": RubricScores(
                기술적합도=5, 데이터요건=4, 컴플라이언스=5, 레퍼런스=4, 컨소시엄=3
            ).model_dump(),
            "reason": "no",
            "missing_tech": ["의료영상 라벨링"],
            "consortium_need": "의료 AI 전문 SI",
        }
    )
