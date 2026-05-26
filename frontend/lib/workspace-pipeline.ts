/**
 * docId별 SSE·폴링을 페이지 전환 후에도 유지 — GPT 스타일 멀티 프로젝트 백그라운드 실행.
 * Review 페이지는 subscribe만 하고 unmount 시 연결을 끊지 않는다.
 */

import { eventStreamUrl, fetchPipelineStatus, type PipelineHistoryEntry } from "@/lib/api";
import type { PipelineEventData } from "@/lib/pipeline";

export type PipelineRuntimeState = {
  docId: string;
  stage: string;
  payload: Record<string, unknown>;
  history: PipelineHistoryEntry[];
  error: string | null;
  timingSummary: { total_elapsed_ms: number; from_cache: boolean } | null;
  updatedAt: number;
};

type Listener = (state: PipelineRuntimeState) => void;

type Runtime = {
  docId: string;
  listeners: Set<Listener>;
  eventSource: EventSource | null;
  pollId: number | null;
  state: PipelineRuntimeState;
};

const runtimes = new Map<string, Runtime>();

const PIPELINE_SSE_STAGES = [
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

function emptyState(docId: string): PipelineRuntimeState {
  return {
    docId,
    stage: "UPLOADED",
    payload: {},
    history: [],
    error: null,
    timingSummary: null,
    updatedAt: Date.now(),
  };
}

function notify(rt: Runtime): void {
  for (const fn of rt.listeners) fn(rt.state);
}

function applyPipelineResponse(rt: Runtime, st: Record<string, unknown>): void {
  const history = (st.history as PipelineHistoryEntry[] | undefined) ?? [];
  const timingSummary = (st.timing_summary as PipelineRuntimeState["timingSummary"]) ?? null;
  rt.state = {
    ...rt.state,
    stage: (st.stage as string) ?? rt.state.stage,
    payload: (st.payload as Record<string, unknown>) ?? {},
    history,
    error: (st.error as string | null) ?? null,
    timingSummary,
    updatedAt: Date.now(),
  };
  notify(rt);
}

function ensureEventSource(rt: Runtime): void {
  if (rt.eventSource) return;
  const src = new EventSource(eventStreamUrl(rt.docId));
  const handler = (e: MessageEvent) => {
    try {
      const ev = JSON.parse(e.data) as PipelineEventData;
      const history = [...rt.state.history];
      history.push({
        stage: ev.stage,
        payload: ev.payload as Record<string, unknown>,
        ts: ev.ts,
      });
      if (history.length > 250) history.splice(0, history.length - 250);
      rt.state = {
        ...rt.state,
        stage: ev.stage,
        payload: ev.payload as Record<string, unknown>,
        history,
        error: ev.error ?? null,
        updatedAt: Date.now(),
      };
      notify(rt);
    } catch {
      /* ignore */
    }
  };
  for (const s of PIPELINE_SSE_STAGES) {
    src.addEventListener(s, handler);
  }
  rt.eventSource = src;
}

function ensurePoll(rt: Runtime): void {
  if (rt.pollId != null) return;
  const poll = () => {
    void fetchPipelineStatus(rt.docId)
      .then((st) => applyPipelineResponse(rt, st as unknown as Record<string, unknown>))
      .catch(() => {
        /* ignore */
      });
  };
  poll();
  rt.pollId = window.setInterval(poll, 3000);
}

function maybeTeardown(rt: Runtime): void {
  if (rt.listeners.size > 0) return;
  if (rt.eventSource) {
    rt.eventSource.close();
    rt.eventSource = null;
  }
  if (rt.pollId != null) {
    window.clearInterval(rt.pollId);
    rt.pollId = null;
  }
}

export function ensurePipelineRuntime(docId: string): Runtime {
  let rt = runtimes.get(docId);
  if (!rt) {
    rt = {
      docId,
      listeners: new Set(),
      eventSource: null,
      pollId: null,
      state: emptyState(docId),
    };
    runtimes.set(docId, rt);
  }
  return rt;
}

export function subscribePipelineRuntime(
  docId: string,
  listener: Listener,
  options?: { bootstrap?: boolean },
): () => void {
  const rt = ensurePipelineRuntime(docId);
  rt.listeners.add(listener);
  listener(rt.state);

  if (options?.bootstrap !== false) {
    ensureEventSource(rt);
    ensurePoll(rt);
    void fetchPipelineStatus(docId)
      .then((st) => applyPipelineResponse(rt, st as unknown as Record<string, unknown>))
      .catch(() => {
        /* ignore */
      });
  }

  return () => {
    rt.listeners.delete(listener);
    maybeTeardown(rt);
  };
}

export function getPipelineRuntimeState(docId: string): PipelineRuntimeState | null {
  return runtimes.get(docId)?.state ?? null;
}
