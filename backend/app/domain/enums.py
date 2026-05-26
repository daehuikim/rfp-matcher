from __future__ import annotations

from enum import StrEnum


class DocumentMime(StrEnum):
    PDF = "application/pdf"
    DOC = "application/msword"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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
    RECOMMENDING = "RECOMMENDING"
    RECOMMENDED = "RECOMMENDED"  # AI 추천 단계 전체 완료
    READY_FOR_REVIEW = "READY_FOR_REVIEW"  # 조견표 추출 완료 — Excel/리뷰 가능
    # 동시 편집 시 다른 사용자의 변경을 listen해 화면을 갱신하기 위한 토픽.
    JUDGEMENT_UPDATED = "JUDGEMENT_UPDATED"
    FAILED = "FAILED"


class ExportMode(StrEnum):
    AI = "ai"
    HUMAN = "human"
    BOTH = "both"
