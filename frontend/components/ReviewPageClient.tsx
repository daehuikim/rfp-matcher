"use client";

import { useCallback, useEffect, useMemo, useRef, useState, startTransition } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import {
  ExtractionProfile,
  Judgement,
  JudgementUpdatedPayload,
  PipelineStatusResponse,
  RequirementView,
  ensurePipeline,
  eventStreamUrl,
  fetchDocumentMeta,
  fetchExtractionProfile,
  listRequirements,
} from "@/lib/api";
import { reconcileDocumentRoute } from "@/lib/document-route";
import { useActiveProjectNavItem } from "@/context/WorkspaceProvider";
import { removeWorkspaceSession } from "@/lib/workspace";
import { ExportPanel } from "@/components/ExportPanel";
import { ProjectTitleEditor } from "@/components/ProjectTitleEditor";
import { RequirementRow } from "@/components/RequirementRow";
import { RequirementTable } from "@/components/RequirementTable";
import { PdfViewerPane } from "@/components/PdfViewerPane";
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

type Props = {
  docId: string;
  initialRequirements: RequirementView[];
  initialPipelineStatus: PipelineStatusResponse | null;
  initialExtractionProfile: ExtractionProfile | null;
};

export default function ReviewPageClient({
  docId,
  initialRequirements,
  initialPipelineStatus,
  initialExtractionProfile,
}: Props) {
  const router = useRouter();
  const editorId = useEditorId();
  const activeNav = useActiveProjectNavItem();

  const { data: docMeta } = useSWR(["document-meta", docId], () => fetchDocumentMeta(docId), {
    refreshInterval: 15000,
  });

  const projectTitle = docMeta?.title ?? activeNav?.title ?? "";
  const sourceFilename = docMeta?.source_filename ?? activeNav?.sourceFilename ?? null;

  const { data, error, isLoading, mutate } = useSWR<RequirementView[]>(
    ["requirements", docId],
    () => listRequirements(docId),
    {
      fallbackData: initialRequirements.length > 0 ? initialRequirements : undefined,
      refreshInterval: (latest) => (latest && latest.length > 0 ? 1500 : 400),
    },
  );

  const rows = data ?? initialRequirements;

  const { data: extractionProfile } = useSWR(
    ["extraction-profile", docId],
    () => fetchExtractionProfile(docId),
    { fallbackData: initialExtractionProfile ?? undefined, refreshInterval: 4000 },
  );

  const refreshRows = useCallback(() => {
    void mutate();
  }, [mutate]);

  const pipeline = usePipelineProgress(
    docId,
    {
      onReady: refreshRows,
      onRowAdded: refreshRows,
      onRecommendProgress: refreshRows,
    },
    initialPipelineStatus,
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const route = await reconcileDocumentRoute(docId);
      if (cancelled) return;
      if (route.action === "redirect") {
        startTransition(() => {
          router.replace(`/review/${route.docId}`);
        });
        return;
      }
      if (route.action === "missing") {
        removeWorkspaceSession(docId);
        startTransition(() => {
          router.replace("/?error=session-expired");
        });
        return;
      }
      if (route.action === "ok") {
        void ensurePipeline(route.docId).catch(() => {
          /* running or complete */
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [docId, router]);

  const [filter, setFilter] = useState<Filter>("all");
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>("all");
  const [view, setView] = useState<"table" | "card">("table");

  // 우측 원본 뷰어 (PDF only)
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerPage, setViewerPage] = useState<number | null>(null);
  const [jumpNonce, setJumpNonce] = useState(0);
  const autoOpened = useRef(false);

  const canViewSource =
    docMeta?.mime === "application/pdf" && docMeta?.has_source_file === true;
  const anyPage = useMemo(() => rows.some((v) => v.requirement.source_page != null), [rows]);

  const openPage = useCallback((p: number) => {
    setViewerPage(p);
    setJumpNonce((n) => n + 1);
    setViewerOpen(true);
  }, []);

  useEffect(() => {
    if (!autoOpened.current && canViewSource && anyPage) {
      autoOpened.current = true;
      setViewerOpen(true);
    }
  }, [canViewSource, anyPage]);

  const showViewer = viewerOpen && canViewSource;
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
    if (!rows.length) return init;
    init.total = rows.length;
    for (const v of rows) {
      const m = v.judgement?.mark || "";
      if (m === "O") init.O++;
      else if (m === "△") init["△"]++;
      else if (m === "X") init.X++;
      else init.pending++;
      if (v.recommendation) init.aiCovered++;
    }
    return init;
  }, [rows]);

  const categoryStats = useMemo(() => {
    if (!rows.length) return [];
    const counts = new Map<string, number>();
    for (const v of rows) {
      const cat = v.requirement.category || "미분류";
      counts.set(cat, (counts.get(cat) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort(([a], [b]) => a.localeCompare(b, "ko"))
      .map(([name, count]) => ({ name, count }));
  }, [rows]);

  const filtered = useMemo(() => {
    if (!rows.length) return [];
    let list = rows;
    if (categoryFilter !== "all") {
      list = list.filter((v) => v.requirement.category === categoryFilter);
    }
    if (filter === "all") return list;
    if (filter === "pending") return list.filter((v) => !v.judgement?.mark);
    return list.filter((v) => v.judgement?.mark === filter);
  }, [rows, filter, categoryFilter]);

  const hasRequirements = rows.length > 0;
  const canExport = hasRequirements || pipeline.extractionReady;
  const categoryFilterLabel = extractionProfile?.has_requirement_category_column
    ? "요건 구분"
    : "분류";

  return (
    <div className={`mx-auto ${view === "table" || showViewer ? "max-w-[110rem]" : "max-w-5xl"}`}>
      <PipelineStatus
        projectTitle={projectTitle}
        sourceFilename={sourceFilename}
        stage={pipeline.stage}
        displayTime={pipeline.displayTime}
        isComplete={pipeline.isComplete}
        stageTimings={pipeline.stageTimings}
        feed={pipeline.feed}
        extractionReady={pipeline.extractionReady || hasRequirements}
        aiComplete={pipeline.aiComplete}
        aiDone={pipeline.aiDone}
        aiTotal={pipeline.aiTotal}
        extractedDone={pipeline.extractedDone}
        extractedTotal={pipeline.extractedTotal}
        llmUsage={pipeline.llmUsage}
        llmModel={pipeline.llmModel}
        llmProvider={pipeline.llmProvider}
        error={pipeline.error}
        timingFromCache={pipeline.timingFromCache}
      />

      <section className="panel mb-6 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <ProjectTitleEditor docId={docId} initialTitle={projectTitle} />

          <ExportPanel docId={docId} disabled={!canExport} />
        </div>

        {extractionProfile &&
          (hasRequirements || pipeline.extractionReady) &&
          !extractionProfile.has_requirement_category_column && (
          <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-[12px] leading-relaxed text-amber-950">
            <p className="font-semibold">분류 명세</p>
            <p className="mt-1">{extractionProfile.spec}</p>
            {extractionProfile.has_inferred_categories && (
              <p className="mt-1 text-amber-800">
                「시스템 추론」·「섹션 구조」 표시는 원문 「요건 구분」이 아닌 자동 부여 분류입니다.
              </p>
            )}
          </div>
        )}

        {hasRequirements && (
          <div className="mt-5 grid grid-cols-2 gap-2 md:grid-cols-5">
            <Stat label="총 요건" value={stats.total} />
            <Stat label="O" value={stats.O} />
            <Stat label="△" value={stats["△"]} />
            <Stat label="X" value={stats.X} />
            <Stat label="AI" value={`${stats.aiCovered}/${stats.total}`} />
          </div>
        )}

        {hasRequirements && categoryStats.length > 0 && view === "card" && (
          <div className="mt-4">
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
              {categoryFilterLabel} ({stats.total}건)
            </p>
            <div className="flex flex-wrap gap-1.5 text-xs">
              <button
                type="button"
                onClick={() => setCategoryFilter("all")}
                className={`pill ${categoryFilter === "all" ? "pill-active" : "pill-idle"}`}
              >
                전체 {stats.total}
              </button>
              {categoryStats.map(({ name, count }) => (
                <button
                  key={name}
                  type="button"
                  title={name}
                  onClick={() => setCategoryFilter(name)}
                  className={`pill max-w-full truncate ${
                    categoryFilter === name ? "pill-active" : "pill-idle"
                  }`}
                >
                  {name} {count}
                </button>
              ))}
            </div>
          </div>
        )}

        {hasRequirements && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
            {(["all", "pending", "O", "△", "X"] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={`pill ${filter === f ? "pill-active" : "pill-idle"}`}
              >
                {f === "all" ? "판정·전체" : f === "pending" ? "미판정" : f}
              </button>
            ))}
            <button
              type="button"
              onClick={() => void mutate()}
              className="pill pill-idle"
            >
              ↻
            </button>

            <span className="ml-auto inline-flex items-center gap-1.5">
              {canViewSource && (
                <button
                  type="button"
                  onClick={() => setViewerOpen((v) => !v)}
                  className={`pill ${showViewer ? "pill-active" : "pill-idle"}`}
                  title="우측에 원본 PDF 뷰어 표시 — 페이지 클릭 시 이동"
                >
                  📄 원본
                </button>
              )}
              <span className="inline-flex overflow-hidden rounded-lg border border-neutral-200">
                {(["table", "card"] as const).map((vmode) => (
                  <button
                    key={vmode}
                    type="button"
                    onClick={() => setView(vmode)}
                    className={`px-3 py-1 font-medium transition ${
                      view === vmode
                        ? "bg-ink-900 text-white"
                        : "bg-white text-neutral-600 hover:bg-neutral-50"
                    }`}
                  >
                    {vmode === "table" ? "표" : "카드"}
                  </button>
                ))}
              </span>
            </span>
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
          <div className="mx-auto mb-3 h-10 w-10 animate-spin rounded-full border-2 border-ktred-100 border-t-ktred-500" />
          <p className="text-sm font-medium text-slate-800">HTML 변환 후 조견표를 한 줄씩 추출 중</p>
          <p className="mt-1 font-mono text-lg font-semibold text-ktred-600">
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

      {hasRequirements && filtered.length === 0 && (
        <div className="panel p-6 text-center text-sm text-neutral-500">
          필터에 해당하는 항목이 없습니다.
        </div>
      )}

      {hasRequirements && filtered.length > 0 && (
        <div className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            {view === "table" ? (
              <RequirementTable
                rows={filtered}
                editorId={editorId}
                remoteByReqId={remoteByReqId}
                categoryFilterLabel={categoryFilterLabel}
                onOpenPage={canViewSource ? openPage : undefined}
              />
            ) : (
              <div className="grid gap-3">
                {filtered.map((v) => (
                  <RequirementRow
                    key={v.requirement.id}
                    view={v}
                    editorId={editorId}
                    remoteUpdate={remoteByReqId[v.requirement.id] ?? null}
                    aiPending={!v.recommendation && !pipeline.aiComplete}
                    onOpenPage={canViewSource ? openPage : undefined}
                  />
                ))}
              </div>
            )}
          </div>

          {showViewer && (
            <aside className="sticky top-4 hidden h-[calc(100vh-2rem)] w-[34rem] shrink-0 lg:block xl:w-[40rem]">
              <PdfViewerPane
                docId={docId}
                page={viewerPage}
                jumpNonce={jumpNonce}
                sourceFilename={sourceFilename}
                onClose={() => setViewerOpen(false)}
              />
            </aside>
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
