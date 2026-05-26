"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  Judgement,
  JudgementUpdatedPayload,
  RequirementView,
  eventStreamUrl,
  listRequirements,
} from "@/lib/api";
import { ExportPanel } from "@/components/ExportPanel";
import { RequirementRow } from "@/components/RequirementRow";
import { PipelineStatus } from "@/components/PipelineStatus";
import { ScrollToTop } from "@/components/ScrollToTop";
import { usePipelineProgress } from "@/hooks/usePipelineProgress";

type Filter = "all" | "O" | "△" | "X" | "pending";
type CategoryFilter = "all" | string;

function useEditorId(): string {
  return useMemo(() => {
    if (typeof window === "undefined") return "anon";
    const KEY = "rfp-matcher-editor-id";
    let id = window.localStorage.getItem(KEY);
    if (!id) {
      id = crypto.randomUUID();
      window.localStorage.setItem(KEY, id);
    }
    return id;
  }, []);
}

export default function ReviewPage({ params }: { params: Promise<{ docId: string }> }) {
  const { docId } = use(params);
  const editorId = useEditorId();

  const { data, error, isLoading, mutate } = useSWR<RequirementView[]>(
    ["requirements", docId],
    () => listRequirements(docId),
    {
      refreshInterval: (latest) => (latest && latest.length > 0 ? 1500 : 400),
    },
  );

  const refreshRows = useCallback(() => {
    void mutate();
  }, [mutate]);

  const pipeline = usePipelineProgress(docId, {
    onReady: refreshRows,
    onRowAdded: refreshRows,
    onRecommendProgress: refreshRows,
  });

  const [filter, setFilter] = useState<Filter>("all");
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>("all");
  const [remoteByReqId, setRemoteByReqId] = useState<
    Record<string, { mark: Judgement; note: string; editor_id: string }>
  >({});

  useEffect(() => {
    const src = new EventSource(eventStreamUrl(docId));
    src.addEventListener("JUDGEMENT_UPDATED", (e: MessageEvent) => {
      try {
        const ev = JSON.parse(e.data) as { payload: JudgementUpdatedPayload };
        const p = ev.payload;
        setRemoteByReqId((prev) => ({
          ...prev,
          [p.requirement_id]: { mark: p.mark, note: p.note, editor_id: p.editor_id },
        }));
      } catch {
        /* ignore */
      }
    });
    return () => src.close();
  }, [docId]);

  const stats = useMemo(() => {
    const init = { total: 0, O: 0, "△": 0, X: 0, pending: 0, aiCovered: 0 };
    if (!data) return init;
    init.total = data.length;
    for (const v of data) {
      const m = v.judgement?.mark || "";
      if (m === "O") init.O++;
      else if (m === "△") init["△"]++;
      else if (m === "X") init.X++;
      else init.pending++;
      if (v.recommendation) init.aiCovered++;
    }
    return init;
  }, [data]);

  const categoryStats = useMemo(() => {
    if (!data) return [];
    const counts = new Map<string, number>();
    for (const v of data) {
      const cat = v.requirement.category || "기타";
      counts.set(cat, (counts.get(cat) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort(([a], [b]) => a.localeCompare(b, "ko"))
      .map(([name, count]) => ({ name, count }));
  }, [data]);

  const filtered = useMemo(() => {
    if (!data) return [];
    let rows = data;
    if (categoryFilter !== "all") {
      rows = rows.filter((v) => v.requirement.category === categoryFilter);
    }
    if (filter === "all") return rows;
    if (filter === "pending") return rows.filter((v) => !v.judgement?.mark);
    return rows.filter((v) => v.judgement?.mark === filter);
  }, [data, filter, categoryFilter]);

  const hasRequirements = (data?.length ?? 0) > 0;
  const canExport = hasRequirements || pipeline.extractionReady;

  return (
    <div className="mx-auto max-w-5xl">
      <PipelineStatus
        stage={pipeline.stage}
        displayTime={pipeline.displayTime}
        feed={pipeline.feed}
        extractionReady={pipeline.extractionReady || hasRequirements}
        aiComplete={pipeline.aiComplete}
        aiDone={pipeline.aiDone}
        aiTotal={pipeline.aiTotal}
        extractedDone={pipeline.extractedDone}
        extractedTotal={pipeline.extractedTotal}
        error={pipeline.error}
      />

      <section className="panel mb-6 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wider text-slate-400">
              Document
            </p>
            <h1 className="mt-0.5 text-xl font-bold tracking-tight">요건 검토</h1>
            <p className="mt-1 font-mono text-xs text-slate-400">{docId.slice(0, 16)}…</p>
          </div>

          <ExportPanel docId={docId} disabled={!canExport} />
        </div>

        {hasRequirements && (
          <div className="mt-5 grid grid-cols-2 gap-2 md:grid-cols-5">
            <Stat label="총 요건" value={stats.total} />
            <Stat label="O" value={stats.O} />
            <Stat label="△" value={stats["△"]} />
            <Stat label="X" value={stats.X} />
            <Stat label="AI" value={`${stats.aiCovered}/${stats.total}`} />
          </div>
        )}

        {hasRequirements && categoryStats.length > 0 && (
          <div className="mt-4">
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              분류별 ({stats.total}건)
            </p>
            <div className="flex flex-wrap gap-1.5 text-xs">
              <button
                type="button"
                onClick={() => setCategoryFilter("all")}
                className={`pill ${
                  categoryFilter === "all"
                    ? "border-violet-300 bg-violet-50 text-violet-800"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                전체 {stats.total}
              </button>
              {categoryStats.map(({ name, count }) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => setCategoryFilter(name)}
                  className={`pill ${
                    categoryFilter === name
                      ? "border-violet-300 bg-violet-50 text-violet-800"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {name} {count}
                </button>
              ))}
            </div>
          </div>
        )}

        {hasRequirements && (
          <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
            {(["all", "pending", "O", "△", "X"] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={`pill ${
                  filter === f
                    ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                {f === "all" ? "판정·전체" : f === "pending" ? "미판정" : f}
              </button>
            ))}
            <button
              type="button"
              onClick={() => void mutate()}
              className="pill border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
            >
              ↻
            </button>
          </div>
        )}
      </section>

      {error && (
        <div className="panel mb-4 border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
          불러오기 실패: {String(error)}
        </div>
      )}

      {!hasRequirements && !isLoading && pipeline.error && (
        <div className="panel border-rose-200 bg-rose-50 p-8 text-center">
          <p className="text-sm font-medium text-rose-800">추출 실패</p>
          <p className="mt-2 text-xs text-rose-700">{pipeline.error}</p>
          <p className="mt-4 text-xs text-slate-500">
            백엔드를 재시작한 뒤 PDF_CONVERTER=pymupdf 로 다시 업로드하세요.
          </p>
          <a href="/" className="btn-primary mt-4 inline-flex">
            홈에서 다시 시도
          </a>
        </div>
      )}

      {!hasRequirements && !isLoading && !pipeline.error && (
        <div className="panel p-8 text-center">
          <div className="mx-auto mb-3 h-10 w-10 animate-spin rounded-full border-2 border-indigo-200 border-t-indigo-600" />
          <p className="text-sm font-medium text-slate-800">HTML 변환 후 조견표를 한 줄씩 추출 중</p>
          <p className="mt-1 font-mono text-lg font-semibold text-indigo-600">
            {pipeline.displayTime}
          </p>
          {pipeline.extractedTotal > 0 && (
            <p className="mt-2 font-mono text-sm text-amber-700">
              {pipeline.extractedDone}/{pipeline.extractedTotal}줄
            </p>
          )}
          <p className="mt-2 text-xs text-slate-500">
            추출된 줄은 아래 목록에 바로 표시됩니다. AI 검토는 조견표 완료 후 한 줄씩 진행됩니다.
          </p>
        </div>
      )}

      {isLoading && !hasRequirements && (
        <div className="grid gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="panel h-28 animate-pulse bg-slate-50" />
          ))}
        </div>
      )}

      {hasRequirements &&
        pipeline.extractedTotal > 0 &&
        pipeline.extractedDone < pipeline.extractedTotal && (
          <div className="panel mb-4 border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            조견표 {pipeline.extractedDone}/{pipeline.extractedTotal}줄 추출 중 — 완료된 줄부터 아래에서
            확인·Excel 다운로드 가능
          </div>
        )}

      {hasRequirements && (
        <div className="grid gap-3">
          {filtered.length === 0 ? (
            <div className="panel p-6 text-center text-sm text-slate-500">
              필터에 해당하는 항목이 없습니다.
            </div>
          ) : (
            filtered.map((v) => (
              <RequirementRow
                key={v.requirement.id}
                view={v}
                editorId={editorId}
                remoteUpdate={remoteByReqId[v.requirement.id] ?? null}
                aiPending={!v.recommendation && !pipeline.aiComplete}
              />
            ))
          )}
        </div>
      )}

      <ScrollToTop />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50/80 p-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className="mt-0.5 text-xl font-bold tabular-nums">{value}</div>
    </div>
  );
}
