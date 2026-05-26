"use client";

import { useEffect, useState } from "react";
import type { CatalogCandidateAudit, MatchedSolutionSku } from "@/lib/api";

type SolutionLike = CatalogCandidateAudit | MatchedSolutionSku;

function solutionLabel(c: SolutionLike): string {
  return c.sku_label || c.solution_name;
}

function solutionHierarchy(c: SolutionLike): string {
  return [c.category_major, c.category_mid, c.category_sub].filter(Boolean).join(" › ");
}

export function CatalogAuditPanel({
  audit,
  matchedSolutions = [],
}: {
  audit: CatalogCandidateAudit[];
  matchedSolutions?: MatchedSolutionSku[];
}) {
  const [open, setOpen] = useState(false);

  if (!audit.length && !matchedSolutions.length) return null;

  const selected = audit.filter((c) => c.selected);
  const excluded = audit.filter((c) => !c.selected);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
        {matchedSolutions.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-neutral-500">매칭 솔루션</span>
            {matchedSolutions.map((s) => (
              <button
                key={s.catalog_id}
                type="button"
                onClick={() => setOpen(true)}
                className="pill border-indigo-200 bg-indigo-50 text-indigo-800 transition hover:border-indigo-300 hover:bg-indigo-100"
                title="클릭하여 상세 보기"
              >
                {solutionLabel(s)}
              </button>
            ))}
          </div>
        )}
        {audit.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="font-medium text-indigo-600 underline-offset-2 hover:underline"
          >
            탐색 후보 {audit.length}건 · 채택 {selected.length} · 제외 {excluded.length} — 상세
            보기
          </button>
        )}
        {audit.length === 0 && matchedSolutions.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="font-medium text-indigo-600 underline-offset-2 hover:underline"
          >
            솔루션 상세 보기
          </button>
        )}
      </div>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/45 p-4 sm:items-center"
          role="dialog"
          aria-modal="true"
          aria-labelledby="catalog-audit-title"
          onClick={() => setOpen(false)}
        >
          <div
            className="panel max-h-[85vh] w-full max-w-2xl overflow-hidden shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
              <div>
                <h3 id="catalog-audit-title" className="text-sm font-semibold text-slate-800">
                  KT 솔루션 매칭 상세
                </h3>
                <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">
                  BM25 탐색 후보와 AI가 채택한 솔루션입니다. 동일 브랜드명이라도 소분류·기능이
                  다르면 별도 항목으로 구분됩니다.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="shrink-0 rounded-lg px-2 py-1 text-slate-400 hover:bg-slate-50 hover:text-slate-700"
                aria-label="닫기"
              >
                ✕
              </button>
            </div>

            <div className="max-h-[70vh] overflow-y-auto px-4 py-3 text-[12px]">
              {(matchedSolutions.length > 0 || selected.length > 0) && (
                <section className="mb-5">
                  <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-emerald-600">
                    채택 솔루션 ({matchedSolutions.length || selected.length})
                  </p>
                  <ul className="space-y-2">
                    {matchedSolutions.length > 0
                      ? matchedSolutions.map((s) => (
                          <AdoptedSolutionRow key={s.catalog_id} solution={s} />
                        ))
                      : selected.map((c) => (
                          <CandidateRow key={c.catalog_id} c={c} />
                        ))}
                  </ul>
                </section>
              )}

              {excluded.length > 0 && (
                <section>
                  <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                    제외 후보 ({excluded.length})
                  </p>
                  <ul className="space-y-2">
                    {excluded.map((c) => (
                      <CandidateRow key={c.catalog_id} c={c} />
                    ))}
                  </ul>
                </section>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function AdoptedSolutionRow({ solution }: { solution: MatchedSolutionSku }) {
  const hierarchy = solutionHierarchy(solution);

  return (
    <li className="rounded-lg border border-emerald-100 bg-emerald-50/50 px-3 py-2.5">
      <p className="font-medium text-slate-800">{solutionLabel(solution)}</p>
      {solution.solution_name && solution.sku_label && solution.sku_label !== solution.solution_name && (
        <p className="mt-0.5 text-[11px] text-slate-600">브랜드: {solution.solution_name}</p>
      )}
      {hierarchy && <p className="mt-0.5 text-[10px] text-slate-500">{hierarchy}</p>}
      {solution.description && (
        <p className="mt-2 text-[11px] leading-relaxed text-slate-700">
          <span className="font-medium text-slate-500">기능 · </span>
          {solution.description}
        </p>
      )}
      <p className="mt-2 font-mono text-[10px] text-slate-400" title="카탈로그 ID">
        {solution.catalog_id}
      </p>
    </li>
  );
}

function CandidateRow({ c }: { c: CatalogCandidateAudit }) {
  const hierarchy = solutionHierarchy(c);

  return (
    <li
      className={`rounded-lg border px-3 py-2.5 ${
        c.selected
          ? "border-emerald-100 bg-emerald-50/50"
          : "border-slate-100 bg-slate-50/80"
      }`}
    >
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-medium text-slate-800">{solutionLabel(c)}</p>
            {c.selected && (
              <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
                채택
              </span>
            )}
          </div>
          {c.solution_name && c.sku_label && c.sku_label !== c.solution_name && (
            <p className="mt-0.5 text-[11px] text-slate-600">브랜드: {c.solution_name}</p>
          )}
          {hierarchy && <p className="mt-0.5 text-[10px] text-slate-500">{hierarchy}</p>}
          <p className="mt-1 font-mono text-[10px] text-slate-400" title="카탈로그 ID">
            {c.catalog_id}
          </p>
        </div>
        <span
          className="ml-auto shrink-0 font-mono text-[10px] text-slate-400"
          title="요건별 BM25 재검색 점수"
        >
          검색 {c.similarity_score.toFixed(2)}
        </span>
      </div>
      {c.description && (
        <p className="mt-2 text-[11px] leading-snug text-slate-700">
          <span className="font-medium text-slate-500">기능 · </span>
          {c.description}
        </p>
      )}
      {!c.selected && c.exclusion_reason && (
        <p className="mt-1.5 text-[11px] leading-snug text-slate-600">
          <span className="font-medium text-slate-500">제외 사유 · </span>
          {c.exclusion_reason}
        </p>
      )}
    </li>
  );
}
