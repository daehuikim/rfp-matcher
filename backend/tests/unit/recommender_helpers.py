from __future__ import annotations

import re

from pydantic import BaseModel

from app.llm.base import Message
from app.phase1.extraction.parsing import split_by_markers
from app.phase2.recommender.rubric import RubricScores


def _extract_requirement_ids(messages: list[Message]) -> list[str]:
    text = messages[0].content if messages else ""
    return re.findall(r"requirement_id=([^\s\n]+)", text)


def _extract_cell_body(messages: list[Message]) -> str:
    text = messages[0].content if messages else ""
    if "[셀 본문]" in text:
        return text.split("[셀 본문]", 1)[1].strip().split("\n\n", 1)[0].strip()
    return ""


def extraction_llm_handler(schema: type[BaseModel], msgs: list[Message]) -> BaseModel:
    """Phase 1 추출 — TableLocator·TableSplitProfiler용 Fake LLM."""
    fields = schema.model_fields
    text = msgs[0].content if msgs else ""

    if "results" in fields and "requirement_id=" not in text:
        indices = [int(m) for m in re.findall(r"index=(\d+)", text)]
        return schema.model_validate(
            {
                "results": [
                    {
                        "table_index": idx,
                        "is_requirements_table": False,
                        "confidence": 0.1,
                    }
                    for idx in indices
                ]
            }
        )

    if "is_requirements_table" in fields:
        return schema.model_validate({"is_requirements_table": False, "confidence": 0.1})

    if "primary_markers" in fields:
        return schema.model_validate(
            {
                "primary_markers": ["①", "②", "③"],
                "split_on_blank_line": True,
                "one_row_one_requirement": False,
                "notes": "fake schema",
            }
        )

    if "atoms" in fields:
        cell = _extract_cell_body(msgs)
        atoms = [a.text for a in split_by_markers(cell) if a.text.strip()]
        if not atoms and cell.strip():
            atoms = [cell.strip()]
        return schema.model_validate({"atoms": atoms})

    if "results" in fields:
        ids = _extract_requirement_ids(msgs) or ["r1"]
        return schema.model_validate(
            {
                "results": [
                    {
                        "requirement_id": rid,
                        "verified_ids": [],
                        "excluded_notes": [],
                        "rubric": RubricScores(
                            기술적합도=1,
                            데이터요건=1,
                            컴플라이언스=2,
                            레퍼런스=2,
                            컨소시엄=1,
                        ).model_dump(),
                        "reason": "기존 카탈로그로 충분히 커버",
                        "missing_tech": [],
                        "consortium_need": None,
                    }
                    for rid in ids
                ]
            }
        )

    return schema.model_validate({})


def batch_yes_handler(schema: type[BaseModel], msgs: list[Message]) -> BaseModel:
    """배치 _BatchVerdict 또는 Phase 1 스키마 모두 처리."""
    text = msgs[0].content if msgs else ""
    if "requirement_id=" in text:
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
    return extraction_llm_handler(schema, msgs)


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
