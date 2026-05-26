"use client";

import { useMemo, useState } from "react";
import { exportUrl } from "@/lib/api";

export type ExportColumnKey =
  | "category"
  | "code"
  | "name"
  | "definition"
  | "detail"
  | "deliverables"
  | "related"
  | "ai_risk"
  | "ai_reason"
  | "matched_solutions"
  | "missing_tech"
  | "consortium"
  | "human_mark"
  | "human_note";

const COLUMN_LABELS: Record<ExportColumnKey, string> = {
  category: "분류",
  code: "코드 (시스템 생성)",
  name: "명칭",
  definition: "정의",
  detail: "세부내용",
  deliverables: "산출정보",
  related: "관련요구사항",
  ai_risk: "AI 리스크",
  ai_reason: "AI 이유",
  matched_solutions: "연관 솔루션",
  missing_tech: "부족 기술",
  consortium: "필요 컨소시엄",
  human_mark: "사람 판정",
  human_note: "사람 메모",
};

const PRESETS: { id: string; label: string; cols: ExportColumnKey[] }[] = [
  {
    id: "original",
    label: "원본만",
    cols: ["category", "name", "detail"],
  },
  {
    id: "standard",
    label: "표준",
    cols: ["category", "name", "definition", "detail", "deliverables", "related"],
  },
  {
    id: "full",
    label: "전체",
    cols: Object.keys(COLUMN_LABELS) as ExportColumnKey[],
  },
];

export function ExportPanel({ docId, disabled }: { docId: string; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"human" | "ai" | "both">("both");
  const [cols, setCols] = useState<ExportColumnKey[]>(PRESETS[1].cols);

  const href = useMemo(
    () => (disabled ? "#" : exportUrl(docId, mode, cols)),
    [docId, mode, cols, disabled],
  );

  function applyPreset(cols: ExportColumnKey[]) {
    setCols(cols);
  }

  function toggleCol(key: ExportColumnKey) {
    setCols((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="btn-primary disabled:opacity-40"
      >
        Excel 내려받기 ↓
      </button>

      {open && !disabled && (
        <div className="absolute right-0 z-20 mt-2 w-80 rounded-xl border border-slate-200 bg-white p-4 shadow-lg">
          <p className="text-xs font-semibold text-slate-700">다운로드 옵션</p>

          <div className="mt-3 flex flex-wrap gap-1">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => applyPreset(p.cols)}
                className="pill border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100"
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="mt-3 max-h-40 space-y-1 overflow-y-auto text-xs">
            {(Object.keys(COLUMN_LABELS) as ExportColumnKey[]).map((key) => (
              <label key={key} className="flex cursor-pointer items-center gap-2 py-0.5">
                <input
                  type="checkbox"
                  checked={cols.includes(key)}
                  onChange={() => toggleCol(key)}
                  className="rounded border-slate-300"
                />
                <span className={key === "code" ? "text-amber-700" : "text-slate-700"}>
                  {COLUMN_LABELS[key]}
                </span>
              </label>
            ))}
          </div>

          <div className="mt-3 flex gap-1 text-xs">
            {(["human", "ai", "both"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`pill ${
                  mode === m
                    ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                    : "border-slate-200 bg-white text-slate-600"
                }`}
              >
                {m === "human" ? "사람" : m === "ai" ? "AI" : "둘 다"}
              </button>
            ))}
          </div>

          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            onClick={() => setOpen(false)}
            className="btn-secondary mt-3 w-full text-center"
          >
            {cols.length}개 칼럼 · 다운로드
          </a>
        </div>
      )}
    </div>
  );
}
