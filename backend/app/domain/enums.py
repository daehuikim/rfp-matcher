from __future__ import annotations

from enum import StrEnum


class DocumentMime(StrEnum):
    PDF = "application/pdf"
    DOC = "application/msword"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    HWP = "application/x-hwp"
    HWPX = "application/vnd.hancom.hwpx"
    HTML = "text/html"


class Judgement(StrEnum):
    YES = "O"
    PARTIAL = "△"
    NO = "X"
    UNSET = ""


class PipelineStage(StrEnum):
    UPLOADED = "UPLOADED"
    CONVERTING = "CONVERTING"
    CONVERTED = "CONVERTED"
    LOCATING = "LOCATING"
    LOCATED = "LOCATED"
    ATOMIZING = "ATOMIZING"
    ATOMIZED = "ATOMIZED"
    CLASSIFYING = "CLASSIFYING"
    CLASSIFIED = "CLASSIFIED"
    CANONICALIZING = "CANONICALIZING"
    CANONICALIZED = "CANONICALIZED"
    RECOMMENDING = "RECOMMENDING"
    RECOMMENDED = "RECOMMENDED"  # AI 추천 단계 전체 완료
    READY_FOR_REVIEW = "READY_FOR_REVIEW"  # 조견표 추출 완료 — Excel/리뷰 가능
    # 동시 편집 시 다른 사용자의 변경을 listen해 화면을 갱신하기 위한 토픽.
    JUDGEMENT_UPDATED = "JUDGEMENT_UPDATED"
    REQUIREMENT_EDITED = "REQUIREMENT_EDITED"        # 칸 편집 — 동시편집 즉시반영
    REQUIREMENTS_CHANGED = "REQUIREMENTS_CHANGED"    # 병합/삭제 등 구조변경 — 재로드
    FAILED = "FAILED"


class ExportMode(StrEnum):
    AI = "ai"
    HUMAN = "human"
    BOTH = "both"


class CategorySource(StrEnum):
    """요건 분류 라벨의 출처 — 원문 조견표 vs 구조 추출 vs 시스템 추론."""

    DOCUMENT_TABLE = "document_table"  # 조견표 「요건 구분」 등 원문 열
    SECTION_HEADING = "section_heading"  # 섹션·중목차(가./나.) 구조
    SYSTEM_INFERRED = "system_inferred"  # canonicalizer·미분류 보정 등
