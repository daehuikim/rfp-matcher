"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { eventStreamUrl, fetchPipelineStatus } from "@/lib/api";
import {
  PipelineEventData,
  PIPELINE_STAGES,
  formatDuration,
} from "@/lib/pipeline";

export type PipelineFeedItem = {
  id: string;
  stage: string;
  snippet: string;
  elapsedTotalMs: number;
  at: number;
};

type ProgressCallbacks = {
  onReady?: () => void;
  onRowAdded?: () => void;
  onRecommendProgress?: () => void;
};

export function usePipelineProgress(docId: string, callbacks?: ProgressCallbacks) {
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
  const readyCalled = useRef(false);
  const feedSeq = useRef(0);
  const lastRecommendDone = useRef(-1);
  const lastStageSnippet = useRef("");

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

    setFeed((prev) => [
      {
        id,
        stage: ev.stage,
        snippet,
        elapsedTotalMs: ev.payload.elapsed_total_ms ?? 0,
        at: Date.now(),
      },
      ...prev.slice(0, 24),
    ]);
  }, []);

  const applyEvent = useCallback(
    (ev: PipelineEventData) => {
      const total = ev.payload.elapsed_total_ms ?? 0;
      setStage(ev.stage);
      setElapsedAnchorMs(total);
      setElapsedAnchorAt(Date.now());
      setDisplayMs(total);

      if (ev.stage === "ATOMIZING" && ev.payload.done != null) {
        setExtractedDone(ev.payload.done);
        if (ev.payload.total != null) setExtractedTotal(ev.payload.total);
        if (ev.payload.requirement_id) callbacksRef.current?.onRowAdded?.();
        pushFeed(ev);
      } else if (ev.stage === "RECOMMENDING") {
        if (ev.payload.done != null) setAiDone(ev.payload.done);
        if (ev.payload.total != null) setAiTotal(ev.payload.total);
        pushFeed(ev);
      } else {
        pushFeed(ev);
      }

      if (ev.stage === "READY_FOR_REVIEW") {
        if (ev.payload.requirements != null) {
          setExtractedTotal(ev.payload.requirements as number);
          setExtractedDone(ev.payload.requirements as number);
        }
        if (!readyCalled.current) {
          readyCalled.current = true;
          callbacksRef.current?.onReady?.();
        }
      }
      if (ev.stage === "FAILED") {
        setError(ev.error ?? "처리 중 오류가 발생했습니다.");
      }
    },
    [pushFeed],
  );

  useEffect(() => {
    readyCalled.current = false;
    feedSeq.current = 0;
    lastRecommendDone.current = -1;
    lastStageSnippet.current = "";
    setStage("UPLOADED");
    setFeed([]);
    setError(null);
    setAiDone(0);
    setAiTotal(0);
    setExtractedDone(0);
    setExtractedTotal(0);
    setElapsedAnchorMs(0);
    setElapsedAnchorAt(Date.now());
    setDisplayMs(0);

    void fetchPipelineStatus(docId)
      .then((st) => {
        applyEvent({
          doc_id: docId,
          stage: st.stage as PipelineEventData["stage"],
          payload: (st.payload ?? {}) as PipelineEventData["payload"],
          ts: st.ts ?? new Date().toISOString(),
          error: st.error ?? null,
        });
      })
      .catch(() => {
        /* ignore */
      });

    const src = new EventSource(eventStreamUrl(docId));
    const handler = (e: MessageEvent) => {
      try {
        applyEvent(JSON.parse(e.data) as PipelineEventData);
      } catch {
        /* ignore */
      }
    };
    for (const s of PIPELINE_STAGES) {
      src.addEventListener(s, handler);
    }
    return () => src.close();
  }, [docId, applyEvent]);

  useEffect(() => {
    const id = window.setInterval(() => {
      setDisplayMs(elapsedAnchorMs + (Date.now() - elapsedAnchorAt));
    }, 1000);
    return () => clearInterval(id);
  }, [elapsedAnchorMs, elapsedAnchorAt]);

  return {
    stage,
    displayTime: formatDuration(displayMs),
    feed,
    error,
    aiDone,
    aiTotal,
    extractedDone,
    extractedTotal,
    extractionReady:
      stage === "READY_FOR_REVIEW" || stage === "RECOMMENDING" || stage === "RECOMMENDED",
    aiComplete: stage === "RECOMMENDED",
  };
}
