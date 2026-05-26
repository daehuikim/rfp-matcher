from __future__ import annotations

from app.domain.enums import PipelineStage

# 프론트 SSE 타임라인·스니펫용 한글 라벨
STAGE_LABEL: dict[PipelineStage, str] = {
    PipelineStage.UPLOADED: "업로드 완료",
    PipelineStage.CONVERTING: "HTML 변환",
    PipelineStage.CONVERTED: "HTML 변환 완료",
    PipelineStage.LOCATING: "조견표 위치 탐지",
    PipelineStage.LOCATED: "조견표 탐지 완료",
    PipelineStage.ATOMIZING: "요건 atomic 분해",
    PipelineStage.ATOMIZED: "atomic 분해 완료",
    PipelineStage.CLASSIFYING: "요건 분류",
    PipelineStage.CLASSIFIED: "분류 완료",
    PipelineStage.READY_FOR_REVIEW: "조견표 추출 완료",
    PipelineStage.RECOMMENDING: "AI 솔루션 매칭",
    PipelineStage.RECOMMENDED: "AI 분석 완료",
    PipelineStage.FAILED: "오류",
    PipelineStage.JUDGEMENT_UPDATED: "판정 갱신",
}

STAGE_SNIPPET: dict[PipelineStage, str] = {
    PipelineStage.CONVERTING: "PDF/DOC/HWPX → HTML 변환 중…",
    PipelineStage.LOCATING: "표 헤더 키워드·LLM으로 조견표 위치 확인 중…",
    PipelineStage.ATOMIZING: "①②③·볼렛 단위로 요건 한 줄씩 분해 중…",
    PipelineStage.CLASSIFYING: "요건구분·카테고리 라벨 정리 중…",
    PipelineStage.READY_FOR_REVIEW: "조견표가 준비됐습니다. Excel 다운로드·검토를 시작하세요.",
    PipelineStage.RECOMMENDING: "KT AI 솔루션 카탈로그와 Rubric 매칭 중…",
    PipelineStage.RECOMMENDED: "모든 요건에 AI 리스크·근거가 채워졌습니다.",
}
