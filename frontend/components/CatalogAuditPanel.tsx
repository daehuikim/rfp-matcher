"use client";

import { useState } from "react";
import type { CatalogCandidateAudit } from "@/lib/api";

export function CatalogAuditPanel({ audit }: { audit: CatalogCandidateAudit[] }) {
  const [open, setOpen] = useState(false);
  if (!audit.length) return null;

  const selected = audit.filter((c) => c.selected);
  const excluded = audit.filter((c) => !c.selected);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-2 text-[11px] font-medium text-indigo-600 underline-offset-2 hover:underline"
      >
        탐색 후보 {audit.length}건 · 채택 {selected.length} · 제외 {excluded.length} — 상세 보기
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 p-4 sm:items-center"
          role="dialog"
          aria-modal="true"
          aria-labelledby="catalog-audit-title"
          onClick={() => setOpen(false)}
        >
          <div
            className="panel max-h-[85vh] w-full max-w-lg overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <h3 id="catalog-audit-title" className="text-sm font-semibold text-slate-800">
                KT 솔루션 탐색 결과
              </h3>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-lg px-2 py-1 text-slate-400 hover:bg-slate-50 hover:text-slate-700"
              >
                ✕
              </button>
            </div>
            <div className="max-h-[70vh] overflow-y-auto px-4 py-3 text-[12px]">
              {selected.length > 0 && (
                <section className="mb-4">
                  <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-emerald-600">
                    채택 ({selected.length})
                  </p>
                  <ul className="space-y-2">
                    {selected.map((c) => (
                      <CandidateRow key={c.catalog_id} c={c} />
                    ))}
                  </ul>
                </section>
              )}
              {excluded.length > 0 && (
                <section>
                  <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                    제외 ({excluded.length})
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

function CandidateRow({ c }: { c: CatalogCandidateAudit }) {
  return (
    <li
      className={`rounded-lg border px-3 py-2 ${
        c.selected ? "border-emerald-100 bg-emerald-50/50" : "border-slate-100 bg-slate-50/80"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-slate-800">{c.solution_name}</span>
        {c.category_major && (
          <span className="pill border-slate-200 bg-white text-slate-500">{c.category_major}</span>
        )}
        <span className="ml-auto font-mono text-[10px] text-slate-400">
          유사도 {c.similarity_score.toFixed(2)}
        </span>
      </div>
      {!c.selected && c.exclusion_reason && (
        <p className="mt-1 text-[11px] leading-snug text-slate-600">{c.exclusion_reason}</p>
      )}
    </li>
  );
}
