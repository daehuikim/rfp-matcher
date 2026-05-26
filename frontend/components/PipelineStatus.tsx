"use client";

import { STAGE_LABEL, PIPELINE_STAGES, formatDuration } from "@/lib/pipeline";
import type { PipelineFeedItem } from "@/hooks/usePipelineProgress";

const EXTRACTION_STAGES = [
  "CONVERTING",
  "LOCATING",
  "ATOMIZING",
  "CLASSIFYING",
  "READY_FOR_REVIEW",
] as const;

type Props = {
  stage: string;
  displayTime: string;
  feed: PipelineFeedItem[];
  extractionReady: boolean;
  aiComplete: boolean;
  aiDone: number;
  aiTotal: number;
  extractedDone: number;
  extractedTotal: number;
  error: string | null;
};

export function PipelineStatus({
  stage,
  displayTime,
  feed,
  extractionReady,
  aiComplete,
  aiDone,
  aiTotal,
  extractedDone,
  extractedTotal,
  error,
}: Props) {
  const currentIdx = PIPELINE_STAGES.indexOf(stage as (typeof PIPELINE_STAGES)[number]);

  return (
    <section className="panel mb-6 overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wider text-slate-400">
            Pipeline
          </p>
          <div className="mt-1 flex items-baseline gap-3">
            <span className="font-mono text-2xl font-bold tabular-nums text-slate-900">
              {displayTime}
            </span>
            <span className="text-sm text-slate-500">
              {STAGE_LABEL[stage] ?? stage}
            </span>
          </div>
        </div>
        {extractedTotal > 0 && !extractionReady && (
          <div className="rounded-lg bg-amber-50 px-3 py-2 text-right">
            <div className="text-[10px] font-medium uppercase text-amber-600">조견표 추출</div>
            <div className="font-mono text-lg font-semibold text-amber-800">
              {extractedDone}/{extractedTotal}
            </div>
          </div>
        )}
        {extractionReady && !aiComplete && aiTotal > 0 && (
          <div className="rounded-lg bg-indigo-50 px-3 py-2 text-right">
            <div className="text-[10px] font-medium uppercase text-indigo-500">AI 검토</div>
            <div className="font-mono text-lg font-semibold text-indigo-700">
              {aiDone}/{aiTotal}
            </div>
          </div>
        )}
      </div>

      {/* Stage stepper */}
      <div className="flex flex-wrap gap-1 border-b border-slate-100 px-5 py-3">
        {EXTRACTION_STAGES.map((s) => {
          const stepIdx = PIPELINE_STAGES.indexOf(s);
          const done = currentIdx > stepIdx || (extractionReady && s === "READY_FOR_REVIEW");
          const active = stage === s;
          return (
            <div
              key={s}
              className={`step-chip ${done ? "step-done" : ""} ${active ? "step-active" : ""}`}
            >
              {STAGE_LABEL[s]}
            </div>
          );
        })}
      </div>

      {error && (
        <div className="mx-5 my-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      {extractionReady && !aiComplete && (
        <div className="border-b border-slate-100 bg-slate-50/80 px-5 py-3">
          <p className="text-xs font-medium text-slate-600">
            ✓ 조견표 추출 완료 — 아래 목록·Excel 다운로드 가능. AI 매칭은 백그라운드에서 계속됩니다.
          </p>
        </div>
      )}

      {/* Activity feed */}
      <div className="max-h-36 overflow-y-auto px-5 py-3">
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Activity
        </p>
        <ul className="space-y-1.5">
          {feed.length === 0 && (
            <li className="text-xs text-slate-400">파이프라인 대기 중…</li>
          )}
          {feed.map((item) => (
            <li key={item.id} className="flex items-start gap-2 text-xs">
              <span className="shrink-0 font-mono text-slate-400">
                {formatDuration(item.elapsedTotalMs)}
              </span>
              <span className="text-slate-600">{item.snippet}</span>
            </li>
          ))}
        </ul>
      </div>

      {aiComplete && (
        <div className="border-t border-emerald-100 bg-emerald-50/60 px-5 py-2 text-xs text-emerald-800">
          AI 분석이 모두 완료되었습니다.
        </div>
      )}
    </section>
  );
}
