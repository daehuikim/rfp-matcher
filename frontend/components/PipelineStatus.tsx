"use client";

import { useState } from "react";
import { STAGE_LABEL, PIPELINE_STAGES, formatDuration } from "@/lib/pipeline";
import type { LlmUsage } from "@/lib/api";
import type { PipelineFeedItem, StageTiming } from "@/hooks/usePipelineProgress";

const EXTRACTION_STAGES = [
  "CONVERTING",
  "LOCATING",
  "ATOMIZING",
  "CLASSIFYING",
  "CANONICALIZING",
  "READY_FOR_REVIEW",
] as const;

const AI_STAGES = ["RECOMMENDING", "RECOMMENDED"] as const;

type Props = {
  stage: string;
  displayTime: string;
  isComplete: boolean;
  stageTimings: StageTiming[];
  feed: PipelineFeedItem[];
  extractionReady: boolean;
  aiComplete: boolean;
  aiDone: number;
  aiTotal: number;
  extractedDone: number;
  extractedTotal: number;
  llmUsage: LlmUsage | null;
  llmModel: string;
  llmProvider: string;
  error: string | null;
  timingFromCache?: boolean;
  projectTitle?: string;
  sourceFilename?: string | null;
};

export function PipelineStatus({
  stage,
  displayTime,
  isComplete,
  stageTimings,
  feed,
  extractionReady,
  aiComplete,
  aiDone,
  aiTotal,
  extractedDone,
  extractedTotal,
  llmUsage,
  llmModel,
  llmProvider,
  error,
  timingFromCache = false,
  projectTitle,
  sourceFilename,
}: Props) {
  const [showPrompts, setShowPrompts] = useState(false);
  const currentIdx = PIPELINE_STAGES.indexOf(stage as (typeof PIPELINE_STAGES)[number]);
  const modelLabel = llmUsage?.model || llmModel || "—";
  const providerLabel = llmUsage?.provider || llmProvider || "—";

  return (
    <section className="panel mb-6 overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
        <div className="min-w-0 flex-1">
          {projectTitle ? (
            <>
              <p className="truncate text-base font-bold tracking-tight text-slate-900">
                {projectTitle}
              </p>
              {sourceFilename && (
                <p className="mt-0.5 truncate text-xs text-slate-500" title={sourceFilename}>
                  원본: {sourceFilename}
                </p>
              )}
            </>
          ) : null}
          <p
            className={`${projectTitle ? "mt-2" : ""} text-[11px] font-medium uppercase tracking-wider text-slate-400`}
          >
            Pipeline
          </p>
          <div className="mt-1 flex items-baseline gap-3">
            <span
              className={`font-mono text-2xl font-bold tabular-nums ${
                isComplete ? "text-emerald-700" : "text-slate-900"
              }`}
            >
              {displayTime}
            </span>
            <span className="text-sm text-slate-500">
              {isComplete ? "완료" : STAGE_LABEL[stage] ?? stage}
              {timingFromCache && !isComplete && " · 기록 시간"}
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
          <div className="rounded-lg bg-ktteal-50 px-3 py-2 text-right">
            <div className="text-[10px] font-medium uppercase text-ktteal-500">AI 검토</div>
            <div className="font-mono text-lg font-semibold text-ktteal-600">
              {aiDone}/{aiTotal}
            </div>
          </div>
        )}
        {llmUsage && llmUsage.total_calls > 0 && (
          <div className="rounded-lg bg-slate-50 px-3 py-2 text-right">
            <div className="text-[10px] font-medium uppercase text-slate-500">LLM 비용 (추정)</div>
            <div className="font-mono text-sm font-semibold text-slate-800">
              ${llmUsage.total_cost_usd.toFixed(4)}
              <span className="ml-1 text-xs font-normal text-slate-500">
                (~₩{Math.round(llmUsage.total_cost_krw).toLocaleString()})
              </span>
            </div>
            <div className="text-[10px] text-slate-400">
              {llmUsage.total_calls} calls · {llmUsage.total_input_tokens.toLocaleString()} in /{" "}
              {llmUsage.total_output_tokens.toLocaleString()} out
            </div>
          </div>
        )}
      </div>

      {/* LLM model & prompts */}
      <div className="border-b border-slate-100 px-5 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs text-slate-600">
            <span className="font-medium text-slate-500">Provider</span>{" "}
            <span className="font-mono">{providerLabel}</span>
            <span className="mx-2 text-slate-300">|</span>
            <span className="font-medium text-slate-500">Model</span>{" "}
            <span className="font-mono text-ktteal-600">{modelLabel}</span>
          </div>
          {llmUsage && llmUsage.recent_calls.length > 0 && (
            <button
              type="button"
              onClick={() => setShowPrompts((v) => !v)}
              className="text-[11px] font-medium text-ktred-600 hover:text-ktred-700"
            >
              {showPrompts ? "프롬프트 숨기기" : "최근 프롬프트 보기"}
            </button>
          )}
        </div>
        {showPrompts && llmUsage?.recent_calls && (
          <ul className="mt-3 max-h-48 space-y-2 overflow-y-auto rounded-lg bg-slate-50 p-3 text-[11px]">
            {llmUsage.recent_calls.map((c, i) => (
              <li key={`${c.purpose}-${i}`} className="border-b border-slate-200/80 pb-2 last:border-0">
                <div className="mb-1 flex flex-wrap gap-2 font-mono text-slate-500">
                  <span className="font-semibold text-slate-700">{c.purpose}</span>
                  <span>
                    {c.input_tokens}+{c.output_tokens} tok
                  </span>
                  <span>${c.cost_usd.toFixed(5)}</span>
                </div>
                <pre className="whitespace-pre-wrap font-sans text-slate-600">{c.prompt_preview}</pre>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Stage stepper — 추출 */}
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

      {/* Stage stepper — AI (추출 완료 후) */}
      {extractionReady && (
        <div className="flex flex-wrap gap-1 border-b border-slate-100 px-5 py-2">
          {AI_STAGES.map((s) => {
            const stepIdx = PIPELINE_STAGES.indexOf(s);
            const done = currentIdx > stepIdx || aiComplete;
            const active = stage === s;
            return (
              <div
                key={s}
                className={`step-chip text-[11px] ${done ? "step-done" : ""} ${active ? "step-active" : ""}`}
              >
                {STAGE_LABEL[s]}
              </div>
            );
          })}
        </div>
      )}

      {error && (
        <div className="mx-5 my-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      {stageTimings.length > 0 && (
        <div className="border-b border-slate-100 px-5 py-3">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            단계별 소요
          </p>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
            {stageTimings.map((t) => (
              <li key={t.stage} className="font-mono tabular-nums">
                <span className="font-sans text-slate-500">{t.label}</span>{" "}
                {formatDuration(t.stepMs)}
                <span className="text-slate-400"> (누적 {formatDuration(t.totalMs)})</span>
              </li>
            ))}
          </ul>
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
            <li className="text-xs text-slate-400">
              {stage === "UPLOADED" ? "파이프라인 시작 중…" : STAGE_LABEL[stage] ?? stage}
            </li>
          )}
          {feed.map((item) => (
            <li key={item.id} className="flex items-start gap-2 text-xs">
              <span className="shrink-0 font-mono text-slate-400">
                +{formatDuration(item.stepMs)}
              </span>
              <span className="shrink-0 font-mono text-slate-300">
                Σ{formatDuration(item.elapsedTotalMs)}
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
