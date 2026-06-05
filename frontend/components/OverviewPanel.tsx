"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetchOverview } from "@/lib/api";

/** RFP 개요 — Excel 개요 시트와 동일 내용(요약·핵심 기술·핵심 RISK)을 웹에서 표시 */
export function OverviewPanel({ docId }: { docId: string }) {
  const { data } = useSWR(["overview", docId], () => fetchOverview(docId), {
    refreshInterval: (d) => (d?.available ? 0 : 5000),
  });
  const [open, setOpen] = useState(true);

  if (!data?.available) return null;
  const techs = data.techs ?? [];
  const risks = (data.risks ?? []).filter((r) => r.some((c) => (c || "").trim()));

  return (
    <section className="panel mb-4 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left transition hover:bg-neutral-50"
      >
        <span className="flex items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-ktred-600">
            RFP 개요
          </span>
          {!open && data.summary && (
            <span className="line-clamp-1 max-w-[60vw] text-xs text-neutral-500">
              {data.summary}
            </span>
          )}
        </span>
        <span className="text-[11px] font-medium text-neutral-400">{open ? "접기 ▴" : "펼치기 ▾"}</span>
      </button>

      {open && (
        <div className="border-t border-neutral-100 px-4 py-3">
          {data.summary && (
            <p className="text-[13px] leading-relaxed text-ink-900">{data.summary}</p>
          )}

          {techs.length > 0 && (
            <div className="mt-3">
              <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
                핵심 기술
              </p>
              <div className="grid gap-1.5 sm:grid-cols-2">
                {techs.map((t, i) => (
                  <div
                    key={i}
                    className="rounded-lg border border-neutral-100 bg-neutral-50/70 px-2.5 py-1.5"
                  >
                    <p className="text-[12px] font-semibold text-ink-900">{t[0]}</p>
                    {t[1] && (
                      <p className="mt-0.5 text-[11px] leading-snug text-neutral-600">{t[1]}</p>
                    )}
                    {t[2] && (
                      <p className="mt-0.5 text-[10px] font-mono text-ktteal-600">{t[2]}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {risks.length > 0 && (
            <div className="mt-3">
              <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-ktred-600">
                핵심 RISK
              </p>
              <ul className="space-y-1">
                {risks.map((r, i) => (
                  <li
                    key={i}
                    className="rounded-lg border border-ktred-100 bg-ktred-50/40 px-2.5 py-1.5 text-[11px] leading-snug text-neutral-700"
                  >
                    {r.filter(Boolean).join(" · ")}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
