"use client";

import { useEffect, useState } from "react";
import { Judgement, RequirementView, categorySourceLabel, patchJudgement } from "@/lib/api";
import { formatRequirementDetail } from "@/lib/formatDetail";
import { CatalogAuditPanel } from "./CatalogAuditPanel";
import { JudgementToggle } from "./JudgementToggle";

function riskColor(r: Judgement): { dot: string; pill: string; label: string } {
  switch (r) {
    case "O":
      return { dot: "bg-emerald-500", pill: "border-emerald-200 bg-emerald-50 text-emerald-700", label: "커버 가능" };
    case "△":
      return { dot: "bg-amber-500", pill: "border-amber-200 bg-amber-50 text-amber-700", label: "조건부" };
    case "X":
      return { dot: "bg-rose-500", pill: "border-rose-200 bg-rose-50 text-rose-700", label: "리스크" };
    default:
      return { dot: "bg-neutral-300", pill: "border-neutral-200 bg-neutral-50 text-neutral-600", label: "미산출" };
  }
}

export function RequirementRow({
  view,
  editorId,
  remoteUpdate,
  aiPending = false,
  onOpenPage,
  onOpenTable,
}: {
  view: RequirementView;
  editorId: string;
  remoteUpdate: { mark: Judgement; note: string; editor_id: string } | null;
  aiPending?: boolean;
  onOpenPage?: (page: number) => void;
  onOpenTable?: (tableIndex: number, anchor?: string) => void;
}) {
  const { requirement: r, recommendation: rec, judgement } = view;
  const [mark, setMark] = useState<Judgement>(judgement?.mark ?? "");
  const [note, setNote] = useState<string>(judgement?.note ?? "");
  const [saving, setSaving] = useState<"idle" | "saving" | "saved" | "err" | "remote">("idle");

  useEffect(() => {
    if (!remoteUpdate) return;
    if (remoteUpdate.editor_id === editorId) return;
    setMark(remoteUpdate.mark);
    setNote(remoteUpdate.note);
    setSaving("remote");
    const t = setTimeout(() => setSaving("idle"), 1500);
    return () => clearTimeout(t);
  }, [remoteUpdate, editorId]);

  async function commit(nextMark: Judgement, nextNote: string) {
    setSaving("saving");
    try {
      await patchJudgement(r.id, nextMark, nextNote, editorId);
      setSaving("saved");
      setTimeout(() => setSaving("idle"), 800);
    } catch {
      setSaving("err");
    }
  }

  const risk = rec ? riskColor(rec.ai_risk) : riskColor("");
  const body = formatRequirementDetail(r.detail || r.name);
  const rawPage = r.source_page;
  const pageNum =
    rawPage == null
      ? null
      : (() => {
          const n = typeof rawPage === "number" ? rawPage : parseInt(String(rawPage), 10);
          return Number.isFinite(n) && n > 0 ? n : null;
        })();
  const tableIndex = typeof r.source_table_index === "number" ? r.source_table_index : null;
  const categorySource = r.category_source ?? "document_table";
  const showCategorySource = categorySource !== "document_table";

  return (
    <article className="glass-card flex flex-col gap-3 p-3.5 md:p-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="pill border-neutral-300 bg-neutral-100 font-semibold text-ink-900">
            {r.category}
          </span>
          {showCategorySource && (
            <span
              className="pill border-amber-200 bg-amber-50 text-amber-800"
              title="원문 「요건 구분」 조견표가 아닌 자동 부여 분류"
            >
              {categorySourceLabel(categorySource)}
            </span>
          )}
          {r.subcategory && (
            <span
              className="pill border-neutral-200 bg-neutral-50 text-neutral-600"
              title={
                r.subcategory_source && r.subcategory_source !== "document_table"
                  ? `소분류 · ${categorySourceLabel(r.subcategory_source)}`
                  : undefined
              }
            >
              {r.subcategory}
              {r.subcategory_source && r.subcategory_source !== "document_table" ? " · 추론" : ""}
            </span>
          )}
          <span className="font-mono text-neutral-400">{r.code}</span>
          {pageNum != null && onOpenPage && (
            <button
              type="button"
              onClick={() => onOpenPage(pageNum)}
              className="pill border-ktred-200 bg-ktred-50 font-medium text-ktred-700 transition hover:border-ktred-300 hover:bg-ktred-100"
              title={`원본 ${pageNum}페이지로 이동`}
            >
              p.{pageNum} ↗
            </button>
          )}
          {pageNum == null && tableIndex != null && onOpenTable && (
            <button
              type="button"
              onClick={() => onOpenTable(tableIndex, r.name)}
              className="pill border-ktred-200 bg-ktred-50 font-medium text-ktred-700 transition hover:border-ktred-300 hover:bg-ktred-100"
              title="원본 문서에서 이 요건이 나온 표로 이동"
            >
              원문 ↗
            </button>
          )}
          {(((pageNum != null && !onOpenPage) || r.source_section)) && (
            <span
              className="pill border-slate-200 bg-slate-50 text-slate-600"
              title={r.source_section ?? undefined}
            >
              {pageNum != null && !onOpenPage ? `p.${pageNum}` : ""}
              {pageNum != null && !onOpenPage && r.source_section ? " · " : ""}
              {r.source_section ? r.source_section.slice(0, 28) : ""}
            </span>
          )}
          <span className={`pill ${risk.pill}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${risk.dot}`} />
            {aiPending ? <>AI 분석 중…</> : <>AI · {rec ? rec.ai_risk || "?" : "—"} {risk.label}</>}
          </span>
        </div>

        <div className="mt-2 text-[14px] leading-relaxed text-neutral-900">
          <p className="font-medium">{body.title}</p>
          {body.bullets.length > 0 && (
            <ul className="mt-1.5 list-none space-y-1 pl-0">
              {body.bullets.map((b, i) => (
                <li key={i} className="flex gap-1.5 text-[13px] text-neutral-800">
                  <span className="shrink-0 text-neutral-400">•</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div
          className={`mt-2.5 rounded-lg border p-2.5 ${
            aiPending
              ? "border-ktteal-100 bg-ktteal-50/40"
              : rec
                ? "border-slate-100 bg-slate-50/80"
                : "border-dashed border-slate-200 bg-slate-50/50"
          }`}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">AI 검토</p>
          {aiPending && (
            <p className="mt-1 text-[12px] text-ktteal-600">KT 솔루션 카탈로그 매칭·Rubric 점수 산출 중…</p>
          )}
          {!aiPending && rec && (
            <>
              <p className="mt-1 text-[12px] leading-relaxed text-neutral-700">
                {rec.ai_reason || "이유 미산출"}
              </p>
              {((rec.matched_solution_skus?.length ?? 0) > 0 ||
                (rec.catalog_audit?.length ?? 0) > 0) && (
                <CatalogAuditPanel
                  audit={rec.catalog_audit ?? []}
                  matchedSolutions={rec.matched_solution_skus}
                />
              )}
              {(rec.matched_solution_skus?.length ?? 0) === 0 &&
                rec.matched_solutions &&
                rec.matched_solutions.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px]">
                    <span className="text-neutral-500">매칭 솔루션</span>
                    {rec.matched_solutions.map((t, i) => (
                      <span
                        key={`match-${i}-${t}`}
                        className="pill border-ktteal-200 bg-ktteal-50 text-ktteal-600"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              {rec.missing_tech.length > 0 && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px]">
                  <span className="text-neutral-500">부족:</span>
                  {rec.missing_tech.map((t, i) => (
                    <span
                      key={`miss-${i}-${t}`}
                      className="pill border-rose-200 bg-rose-50 text-rose-700"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              )}
              {rec.consortium_need && (
                <p className="mt-1 text-[11px] text-neutral-500">
                  컨소시엄: <span className="text-neutral-700">{rec.consortium_need}</span>
                </p>
              )}
            </>
          )}
          {!aiPending && !rec && (
            <p className="mt-1 text-[12px] text-slate-500">AI 분석 대기 중</p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2">
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
          Human
        </span>
        <JudgementToggle
          variant="square"
          size="compact"
          value={mark}
          onChange={(next) => {
            setMark(next);
            void commit(next, note);
          }}
        />
        <input
          className="min-w-[8rem] flex-1 rounded-md border border-neutral-200 bg-white px-2 py-1 text-[11px] placeholder:text-neutral-400 focus:border-ktred-400 focus:outline-none focus:ring-1 focus:ring-ktred-100"
          value={note}
          placeholder="메모"
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => void commit(mark, note)}
        />
        <span className="text-[10px] text-slate-400">
          {saving === "saving" && "저장…"}
          {saving === "saved" && <span className="text-emerald-600">✓</span>}
          {saving === "remote" && <span className="text-ktred-600">↻</span>}
          {saving === "err" && <span className="text-rose-600">실패</span>}
        </span>
      </div>
    </article>
  );
}
