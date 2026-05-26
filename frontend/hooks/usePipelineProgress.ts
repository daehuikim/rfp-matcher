"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { subscribePipelineRuntime } from "@/lib/workspace-pipeline";
import {
  fetchLlmUsage,
  fetchPipelineStatus,
  type LlmUsage,
  type PipelineHistoryEntry,
} from "@/lib/api";
import {
  PipelineEventData,
  STAGE_LABEL,
  formatDuration,
} from "@/lib/pipeline";

/** 마일스톤 이벤트 시 방금 끝난 단계 */
const STAGE_COMPLETED_ON: Record<string, string> = {
  CONVERTED: "CONVERTING",
  LOCATED: "LOCATING",
  ATOMIZED: "ATOMIZING",
  CLASSIFIED: "CLASSIFYING",
  CANONICALIZED: "CANONICALIZING",
  READY_FOR_REVIEW: "READY_FOR_REVIEW",
  RECOMMENDED: "RECOMMENDING",
};

export type StageTiming = {
  stage: string;
  label: string;
  stepMs: number;
  totalMs: number;
};

export type PipelineFeedItem = {
  id: string;
  stage: string;
  snippet: string;
  stepMs: number;
  elapsedTotalMs: number;
  at: number;
};

type ProgressCallbacks = {
  onReady?: () => void;
  onRowAdded?: () => void;
  onRecommendProgress?: () => void;
};

type PipelineStatusSnapshot = {
  stage: string;
  payload: Record<string, unknown>;
  history?: PipelineHistoryEntry[];
  error?: string | null;
  llm_provider?: string;
  llm_model?: string;
  llm_usage?: LlmUsage;
  timing_summary?: {
    total_elapsed_ms: number;
    from_cache?: boolean;
    recorded_total_ms?: number | null;
  };
};

function resolveTotalElapsedMs(st: PipelineStatusSnapshot): number {
  const direct = st.timing_summary?.total_elapsed_ms ?? 0;
  if (direct > 0) return direct;
  const payloadTotal = (st.payload?.elapsed_total_ms as number | undefined) ?? 0;
  if (payloadTotal > 0) return payloadTotal;
  if (st.history?.length) {
    let max = 0;
    for (const entry of st.history) {
      max = Math.max(max, entry.payload?.elapsed_total_ms ?? 0);
    }
    return max;
  }
  return 0;
}

export function usePipelineProgress(
  docId: string,
  callbacks?: ProgressCallbacks,
  initialStatus?: PipelineStatusSnapshot | null,
) {
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  const [stage, setStage] = useState("UPLOADED");
  const [elapsedAnchorMs, setElapsedAnchorMs] = useState(0);
  const [elapsedAnchorAt, setElapsedAnchorAt] = useState(() => Date.now());
  const [displayMs, setDisplayMs] = useState(0);
  const [feed, setFeed] = useState<PipelineFeedItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [aiDone, setAiDone] = useState(0);
  const [aiTotal, setAiTotal] = useState(0);
  const [extractedDone, setExtractedDone] = useState(0);
  const [extractedTotal, setExtractedTotal] = useState(0);
  const [isComplete, setIsComplete] = useState(false);
  const [stageTimings, setStageTimings] = useState<StageTiming[]>([]);
  const [llmUsage, setLlmUsage] = useState<LlmUsage | null>(null);
  const [llmModel, setLlmModel] = useState<string>("");
  const [llmProvider, setLlmProvider] = useState<string>("");
  const [timingFromCache, setTimingFromCache] = useState(false);
  const [docMissing, setDocMissing] = useState(false);
  const readyCalled = useRef(false);
  const isCompleteRef = useRef(false);
  const feedSeq = useRef(0);
  const lastRecommendDone = useRef(-1);
  const lastStageSnippet = useRef("");
  const elapsedBestRef = useRef(0);
  const pageStartedRef = useRef(Date.now());

  const syncElapsedClock = useCallback((totalMs: number, force = false) => {
    if (!force && totalMs <= elapsedBestRef.current) return;
    elapsedBestRef.current = totalMs;
    setElapsedAnchorMs(totalMs);
    setElapsedAnchorAt(Date.now());
    setDisplayMs(totalMs > 0 ? totalMs : Date.now() - pageStartedRef.current);
  }, []);

  const pushFeed = useCallback((ev: PipelineEventData) => {
    const done = ev.payload.done;
    const total = ev.payload.total;
    const snippet =
      ev.payload.snippet ??
      (ev.stage === "RECOMMENDING" && done != null
        ? `AI 검토 ${done}/${total ?? "?"}`
        : ev.stage === "ATOMIZING" && done != null
          ? `조견표 ${done}/${total ?? "?"}`
          : ev.stage);

    if (ev.stage === "RECOMMENDING") {
      if (done == null || done === lastRecommendDone.current) return;
      lastRecommendDone.current = done;
      callbacksRef.current?.onRecommendProgress?.();
    } else if (ev.stage === "ATOMIZING" && done != null) {
      /* 한 줄마다 피드 추가 */
    } else {
      const sig = `${ev.stage}|${snippet}`;
      if (lastStageSnippet.current === sig) return;
      lastStageSnippet.current = sig;
    }

    feedSeq.current += 1;
    const id = `${ev.stage}-${feedSeq.current}-${ev.ts ?? Date.now()}`;
    const stepMs = ev.payload.elapsed_ms ?? 0;

    setFeed((prev) => [
      {
        id,
        stage: ev.stage,
        snippet,
        stepMs,
        elapsedTotalMs: ev.payload.elapsed_total_ms ?? 0,
        at: Date.now(),
      },
      ...prev.slice(0, 24),
    ]);
  }, []);

  const recordMilestone = useCallback((ev: PipelineEventData) => {
    const completed = STAGE_COMPLETED_ON[ev.stage];
    if (!completed) return;
    const stepMs = ev.payload.elapsed_ms ?? 0;
    const totalMs = ev.payload.elapsed_total_ms ?? 0;
    setStageTimings((list) => {
      if (list.some((t) => t.stage === completed)) return list;
      return [
        ...list,
        {
          stage: completed,
          label: STAGE_LABEL[completed] ?? completed,
          stepMs,
          totalMs,
        },
      ];
    });
  }, []);

  const buildFromHistory = useCallback((history: PipelineHistoryEntry[]) => {
    const timings: StageTiming[] = [];
    const feedItems: PipelineFeedItem[] = [];
    const seenMilestone = new Set<string>();
    const seenFeed = new Set<string>();
    let seq = 0;

    for (const entry of history) {
      const ev: PipelineEventData = {
        doc_id: docId,
        stage: entry.stage as PipelineEventData["stage"],
        payload: entry.payload ?? {},
        ts: entry.ts,
      };
      const completed = STAGE_COMPLETED_ON[ev.stage];
      if (completed && !seenMilestone.has(completed)) {
        seenMilestone.add(completed);
        timings.push({
          stage: completed,
          label: STAGE_LABEL[completed] ?? completed,
          stepMs: ev.payload.elapsed_ms ?? 0,
          totalMs: ev.payload.elapsed_total_ms ?? 0,
        });
      }

      const done = ev.payload.done;
      const total = ev.payload.total;
      const snippet =
        ev.payload.snippet ??
        (ev.stage === "RECOMMENDING" && done != null
          ? `AI 검토 ${done}/${total ?? "?"}`
          : ev.stage === "ATOMIZING" && done != null
            ? `조견표 ${done}/${total ?? "?"}`
            : ev.stage);

      let feedKey: string;
      if (ev.stage === "RECOMMENDING" && done != null) {
        feedKey = `REC|${done}`;
      } else if (ev.stage === "ATOMIZING" && done != null) {
        feedKey = `ATOM|${done}`;
      } else {
        feedKey = `${ev.stage}|${snippet}`;
      }
      if (!seenFeed.has(feedKey)) {
        seenFeed.add(feedKey);
        seq += 1;
        feedItems.unshift({
          id: `h-${seq}-${feedKey}`,
          stage: ev.stage,
          snippet,
          stepMs: ev.payload.elapsed_ms ?? 0,
          elapsedTotalMs: ev.payload.elapsed_total_ms ?? 0,
          at: Date.parse(entry.ts) || Date.now(),
        });
      }
    }

    return {
      timings,
      feed: feedItems.slice(0, 25),
    };
  }, [docId]);

  const applyStatusSnapshot = useCallback(
    (st: PipelineStatusSnapshot) => {
      const payload = (st.payload ?? {}) as PipelineEventData["payload"];
      const total = resolveTotalElapsedMs(st);
      setTimingFromCache(Boolean(st.timing_summary?.from_cache));
      setStage(st.stage);
      syncElapsedClock(total);

      if (st.history && st.history.length > 0) {
        const built = buildFromHistory(st.history);
        setStageTimings(built.timings);
        setFeed(built.feed);
        const last = st.history[st.history.length - 1];
        const lastPayload = (last.payload ?? {}) as Record<string, unknown>;
        if (last.stage === "READY_FOR_REVIEW" && lastPayload.requirements != null) {
          const n = lastPayload.requirements as number;
          setExtractedTotal(n);
          setExtractedDone(n);
        }
        if (last.stage === "RECOMMENDED") {
          isCompleteRef.current = true;
          setIsComplete(true);
          if (lastPayload.recommendations != null) {
            const n = lastPayload.recommendations as number;
            setAiTotal(n);
            setAiDone(n);
          }
        }
        if (last.stage === "RECOMMENDING") {
          if (lastPayload.done != null) setAiDone(lastPayload.done as number);
          if (lastPayload.total != null) setAiTotal(lastPayload.total as number);
        }
      }

      if (st.stage === "READY_FOR_REVIEW" || st.stage === "RECOMMENDING" || st.stage === "RECOMMENDED") {
        if (!readyCalled.current) {
          readyCalled.current = true;
          callbacksRef.current?.onReady?.();
        }
      }
      if (st.error) setError(st.error);
    },
    [buildFromHistory, syncElapsedClock],
  );

  const applyEvent = useCallback(
    (ev: PipelineEventData) => {
      const total = ev.payload.elapsed_total_ms ?? 0;
      recordMilestone(ev);

      const finished = ev.stage === "RECOMMENDED" || ev.stage === "FAILED";
      if (finished) {
        isCompleteRef.current = true;
        setIsComplete(true);
        syncElapsedClock(total, true);
        if (ev.stage === "RECOMMENDED") {
          setStageTimings((list) => {
            if (list.some((t) => t.stage === "__COMPLETE__")) return list;
            return [
              ...list,
              {
                stage: "__COMPLETE__",
                label: "전체 완료",
                stepMs: ev.payload.elapsed_ms ?? 0,
                totalMs: total,
              },
            ];
          });
        }
      } else if (!isCompleteRef.current) {
        syncElapsedClock(total);
      }
      setStage(ev.stage);

      const payload = ev.payload as Record<string, unknown>;
      const usage = payload.llm_usage as LlmUsage | undefined;
      if (usage?.model) setLlmUsage(usage);
      if (typeof payload.llm_model === "string") setLlmModel(payload.llm_model);
      if (typeof payload.llm_provider === "string") {
        setLlmProvider(payload.llm_provider);
      }

      if (ev.stage === "ATOMIZING" && ev.payload.done != null) {
        setExtractedDone(ev.payload.done);
        if (ev.payload.total != null) setExtractedTotal(ev.payload.total);
        if (payload.requirement_id) callbacksRef.current?.onRowAdded?.();
        pushFeed(ev);
      } else if (ev.stage === "RECOMMENDING") {
        if (ev.payload.done != null) setAiDone(ev.payload.done);
        if (ev.payload.total != null) setAiTotal(ev.payload.total);
        const done = ev.payload.done;
        const aiTotalEv = ev.payload.total;
        if (
          !isCompleteRef.current &&
          done != null &&
          aiTotalEv != null &&
          aiTotalEv > 0 &&
          done >= aiTotalEv
        ) {
          isCompleteRef.current = true;
          setIsComplete(true);
          syncElapsedClock(total, true);
        }
        pushFeed(ev);
      } else {
        pushFeed(ev);
      }

      if (ev.stage === "READY_FOR_REVIEW") {
        if (ev.payload.requirements != null) {
          const n = ev.payload.requirements as number;
          setExtractedTotal(n);
          setExtractedDone(n);
        }
        if (!readyCalled.current) {
          readyCalled.current = true;
          callbacksRef.current?.onReady?.();
        }
      }
      if (ev.stage === "RECOMMENDED") {
        const n = payload.recommendations as number | undefined;
        if (n != null && n > 0) {
          setAiTotal(n);
          setAiDone(n);
        }
      }
      if (ev.stage === "FAILED") {
        setError(ev.error ?? "처리 중 오류가 발생했습니다.");
      }
    },
    [pushFeed, recordMilestone, syncElapsedClock],
  );

  useEffect(() => {
    readyCalled.current = false;
    isCompleteRef.current = false;
    setDocMissing(false);
    feedSeq.current = 0;
    lastRecommendDone.current = -1;
    lastStageSnippet.current = "";
    elapsedBestRef.current = 0;
    pageStartedRef.current = Date.now();
    setStage("UPLOADED");
    setFeed([]);
    setError(null);
    setAiDone(0);
    setAiTotal(0);
    setExtractedDone(0);
    setExtractedTotal(0);
    setIsComplete(false);
    setStageTimings([]);
    setElapsedAnchorMs(0);
    setElapsedAnchorAt(Date.now());
    setDisplayMs(0);
    setTimingFromCache(false);

    if (initialStatus) {
      if (typeof initialStatus.llm_model === "string") setLlmModel(initialStatus.llm_model);
      if (typeof initialStatus.llm_provider === "string") setLlmProvider(initialStatus.llm_provider);
      if (initialStatus.llm_usage) setLlmUsage(initialStatus.llm_usage);
      applyStatusSnapshot(initialStatus);
    }

    const unsubRuntime = subscribePipelineRuntime(docId, (rt) => {
      applyStatusSnapshot({
        stage: rt.stage,
        payload: rt.payload,
        history: rt.history,
        error: rt.error,
        timing_summary: rt.timingSummary ?? undefined,
      });
    });

    void fetchPipelineStatus(docId)
      .then((st) => {
        if (typeof st.llm_model === "string") setLlmModel(st.llm_model);
        if (typeof st.llm_provider === "string") setLlmProvider(st.llm_provider);
        if (st.llm_usage) setLlmUsage(st.llm_usage);
        applyStatusSnapshot(st);
        if (!st.history?.length) {
          applyEvent({
            doc_id: docId,
            stage: st.stage as PipelineEventData["stage"],
            payload: (st.payload ?? {}) as PipelineEventData["payload"],
            ts: st.ts ?? new Date().toISOString(),
            error: st.error ?? null,
          });
        }
      })
      .catch((err: unknown) => {
        const msg = String(err);
        if (msg.includes("404")) {
          setDocMissing(true);
          setError("프로젝트 세션이 만료되었습니다. 홈에서 다시 시작하세요.");
          isCompleteRef.current = true;
          setIsComplete(true);
        }
      });

    return () => {
      unsubRuntime();
    };
  }, [docId, applyEvent, applyStatusSnapshot, initialStatus]);

  useEffect(() => {
    if (isComplete || docMissing) return;
    const poll = () => {
      void fetchPipelineStatus(docId)
        .then((st) => {
          applyStatusSnapshot(st);
          if (st.llm_usage) setLlmUsage(st.llm_usage);
          if (typeof st.llm_model === "string") setLlmModel(st.llm_model);
          if (typeof st.llm_provider === "string") setLlmProvider(st.llm_provider);
        })
        .catch((err: unknown) => {
          const msg = String(err);
          if (msg.includes("404")) {
            setDocMissing(true);
            setError("프로젝트 세션이 만료되었습니다. 홈에서 다시 시작하세요.");
            isCompleteRef.current = true;
            setIsComplete(true);
          }
        });
    };
    poll();
    const id = window.setInterval(poll, 2500);
    return () => clearInterval(id);
  }, [docId, isComplete, docMissing, applyStatusSnapshot]);

  useEffect(() => {
    if (isComplete || docMissing) return;
    const poll = () => {
      void fetchLlmUsage(docId)
        .then((u) => {
          if (u.total_calls > 0 || u.model) setLlmUsage(u);
        })
        .catch(() => {
          /* ignore */
        });
    };
    poll();
    const id = window.setInterval(poll, 4000);
    return () => clearInterval(id);
  }, [docId, isComplete, docMissing]);

  useEffect(() => {
    if (isComplete) return;
    const id = window.setInterval(() => {
      const serverMs = elapsedBestRef.current;
      if (serverMs > 0) {
        setDisplayMs(serverMs + (Date.now() - elapsedAnchorAt));
      } else {
        setDisplayMs(Date.now() - pageStartedRef.current);
      }
    }, 250);
    return () => clearInterval(id);
  }, [isComplete, elapsedAnchorAt]);

  return {
    stage,
    displayTime: formatDuration(displayMs),
    timingFromCache,
    isComplete,
    stageTimings,
    feed,
    error,
    aiDone,
    aiTotal,
    extractedDone,
    extractedTotal,
    llmUsage,
    llmModel,
    llmProvider,
    extractionReady:
      stage === "READY_FOR_REVIEW" || stage === "RECOMMENDING" || stage === "RECOMMENDED",
    aiComplete: stage === "RECOMMENDED",
  };
}
