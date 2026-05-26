/** Pipeline SSE stage names (must match backend PipelineStage). */
export const PIPELINE_STAGES = [
  "UPLOADED",
  "CONVERTING",
  "CONVERTED",
  "LOCATING",
  "LOCATED",
  "ATOMIZING",
  "ATOMIZED",
  "CLASSIFYING",
  "CLASSIFIED",
  "CANONICALIZING",
  "CANONICALIZED",
  "READY_FOR_REVIEW",
  "RECOMMENDING",
  "RECOMMENDED",
  "FAILED",
] as const;

export type PipelineStageName = (typeof PIPELINE_STAGES)[number];

export type PipelineEventData = {
  doc_id: string;
  stage: PipelineStageName;
  payload: {
    elapsed_ms?: number;
    elapsed_total_ms?: number;
    snippet?: string;
    done?: number;
    total?: number;
    requirements?: number;
    atoms?: number;
    tables?: number;
    step?: string;
  };
  ts: string;
  error?: string | null;
};

export const STAGE_LABEL: Record<string, string> = {
  UPLOADED: "업로드",
  CONVERTING: "HTML 변환",
  CONVERTED: "변환 완료",
  LOCATING: "조견표 탐지",
  LOCATED: "탐지 완료",
  ATOMIZING: "atomic 분해",
  ATOMIZED: "분해 완료",
  CLASSIFYING: "요건 분류",
  CLASSIFIED: "분류 완료",
  CANONICALIZING: "분류 정규화",
  CANONICALIZED: "정규화 완료",
  READY_FOR_REVIEW: "조견표 준비",
  RECOMMENDING: "AI 매칭",
  RECOMMENDED: "AI 완료",
  FAILED: "오류",
};

/** e.g. 0m 2s, or <1s for sub-second steps */
export function formatDuration(ms: number): string {
  if (ms > 0 && ms < 1000) return "<1s";
  const sec = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

export function isExtractionDone(stage: string): boolean {
  const order = PIPELINE_STAGES.indexOf(stage as PipelineStageName);
  const ready = PIPELINE_STAGES.indexOf("READY_FOR_REVIEW");
  return order >= ready && stage !== "FAILED";
}

export function isAiDone(stage: string): boolean {
  return stage === "RECOMMENDED" || stage === "FAILED";
}
