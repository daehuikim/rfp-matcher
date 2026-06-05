"use client";

import { useEffect } from "react";
import type {
  CatalogCandidateAudit,
  Judgement,
  MatchedSolutionSku,
  Recommendation,
  Requirement,
} from "@/lib/api";

function verdictStyle(r: Judgement): { cls: string; label: string } {
  switch (r) {
    case "O":
      return { cls: "border-ktteal-300 bg-ktteal-50 text-ktteal-600", label: "커버 가능" };
    case "△":
      return { cls: "border-amber-300 bg-amber-50 text-amber-700", label: "조건부" };
    case "X":
      return { cls: "border-ktred-300 bg-ktred-50 text-ktred-700", label: "리스크" };
    default:
      return { cls: "border-neutral-200 bg-neutral-50 text-neutral-500", label: "미산출" };
  }
}

function hierarchy(c: CatalogCandidateAudit | MatchedSolutionSku): string {
  return [c.category_major, c.category_mid, c.category_sub].filter(Boolean).join(" › ");
}

/** AI 판정 클릭 시 — 판정·설명·보유/부족 기술·검색 내역(점수·채택/제외 사유)을 explainable하게 */
export function AiVerdictModal({
  open,
  onClose,
  requirement: r,
  recommendation: rec,
}: {
  open: boolean;
  onClose: () => void;
  requirement: Requirement;
  recommendation: Recommendation | null;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const v = verdictStyle(rec?.ai_risk ?? "");
  const audit = rec?.catalog_audit ?? [];
  const adopted = audit.filter((c) => c.selected);
  const excluded = audit.filter((c) => !c.selected);
  const matchedSkus = rec?.matched_solution_skus ?? [];
  const matchedNames = rec?.matched_solutions ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-ink-900/45 p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ai-verdict-title"
      onClick={onClose}
    >
      <div
        className="panel max-h-[88vh] w-full max-w-2xl overflow-hidden shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-neutral-100 px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[11px] text-neutral-400">{r.code}</span>
              <span className={`pill ${v.cls}`}>
                AI · {rec?.ai_risk || "?"} {v.label}
              </span>
            </div>
            <h3 id="ai-verdict-title" className="mt-1 text-sm font-semibold text-ink-900">
              {r.name || r.detail?.slice(0, 60)}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-lg px-2 py-1 text-neutral-400 hover:bg-neutral-50 hover:text-ink-900"
            aria-label="닫기"
          >
            ✕
          </button>
        </div>

        <div className="max-h-[74vh] overflow-y-auto px-4 py-3 text-[12px]">
          {/* AI 설명 */}
          <section className="mb-4">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
              AI 설명
            </p>
            <p className="leading-relaxed text-neutral-700">{rec?.ai_reason || "이유 미산출"}</p>
          </section>

          {/* KT 보유 / 부족 기술 */}
          <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ktteal-600">
                KT 보유 기술
              </p>
              {matchedSkus.length > 0 || matchedNames.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {(matchedSkus.length > 0
                    ? matchedSkus.map((s) => s.sku_label || s.solution_name)
                    : matchedNames
                  ).map((t, i) => (
                    <span
                      key={`${t}-${i}`}
                      className="pill border-ktteal-200 bg-ktteal-50 text-ktteal-600"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-neutral-400">—</p>
              )}
            </div>
            <div>
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ktred-600">
                부족 기술
              </p>
              {(rec?.missing_tech?.length ?? 0) > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {rec!.missing_tech.map((t, i) => (
                    <span
                      key={`${t}-${i}`}
                      className="pill border-ktred-200 bg-ktred-50 text-ktred-700"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-neutral-400">—</p>
              )}
            </div>
          </div>

          {rec?.consortium_need && (
            <section className="mb-4">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
                컨소시엄 필요
              </p>
              <p className="text-neutral-700">{rec.consortium_need}</p>
            </section>
          )}

          {/* 검색 내역 — 카탈로그 후보 점수·채택/제외 사유 */}
          {audit.length > 0 ? (
            <section>
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
                검색 내역 · 후보 {audit.length} · 채택 {adopted.length} · 제외 {excluded.length}
              </p>
              <ul className="space-y-2">
                {audit.map((c) => (
                  <li
                    key={c.catalog_id}
                    className={`rounded-lg border px-3 py-2.5 ${
                      c.selected
                        ? "border-ktteal-100 bg-ktteal-50/50"
                        : "border-neutral-100 bg-neutral-50/70"
                    }`}
                  >
                    <div className="flex flex-wrap items-start gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="font-medium text-ink-900">
                            {c.sku_label || c.solution_name}
                          </p>
                          {c.selected ? (
                            <span className="rounded bg-ktteal-100 px-1.5 py-0.5 text-[9px] font-semibold text-ktteal-600">
                              채택
                            </span>
                          ) : (
                            <span className="rounded bg-neutral-200 px-1.5 py-0.5 text-[9px] font-semibold text-neutral-600">
                              제외
                            </span>
                          )}
                        </div>
                        {hierarchy(c) && (
                          <p className="mt-0.5 text-[10px] text-neutral-500">{hierarchy(c)}</p>
                        )}
                      </div>
                      <span
                        className="ml-auto shrink-0 font-mono text-[10px] text-neutral-400"
                        title="요건별 BM25 재검색 점수"
                      >
                        검색 {c.similarity_score.toFixed(2)}
                      </span>
                    </div>
                    {c.description && (
                      <p className="mt-1.5 leading-snug text-neutral-700">
                        <span className="font-medium text-neutral-500">기능 · </span>
                        {c.description}
                      </p>
                    )}
                    {!c.selected && c.exclusion_reason && (
                      <p className="mt-1 leading-snug text-neutral-600">
                        <span className="font-medium text-neutral-500">제외 사유 · </span>
                        {c.exclusion_reason}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <p className="text-[11px] text-neutral-400">검색 내역(카탈로그 후보) 데이터가 없습니다.</p>
          )}
        </div>
      </div>
    </div>
  );
}
