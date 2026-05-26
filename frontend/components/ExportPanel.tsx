"use client";

import { useEffect, useMemo, useState } from "react";
import { exportUrl, fetchExportColumns, type ExportColumnInfo } from "@/lib/api";

const PRESET_LABELS: Record<string, string> = {
  original: "원본만",
  standard: "표준",
  full: "전체",
};

export function ExportPanel({ docId, disabled }: { docId: string; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"human" | "ai" | "both">("both");
  const [preset, setPreset] = useState("standard");
  const [applicable, setApplicable] = useState<ExportColumnInfo[]>([]);
  const [cols, setCols] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || disabled) return;
    let cancelled = false;
    setLoading(true);
    fetchExportColumns(docId, mode, preset)
      .then((data) => {
        if (cancelled) return;
        setApplicable(data.applicable);
        setCols(data.selected);
      })
      .catch(() => {
        if (!cancelled) setCols(["detail"]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, disabled, docId, mode, preset]);

  const href = useMemo(
    () => (disabled || cols.length === 0 ? "#" : exportUrl(docId, mode, cols)),
    [docId, mode, cols, disabled],
  );

  function toggleCol(key: string) {
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
          <p className="mt-1 text-[10px] text-slate-500">
            이 문서에 값이 있는 칼럼만 표시됩니다 (JB·하나 등 형식 자동 반영).
          </p>

          <div className="mt-3 flex flex-wrap gap-1">
            {Object.keys(PRESET_LABELS).map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => setPreset(id)}
                className={`pill ${
                  preset === id
                    ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                    : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100"
                }`}
              >
                {PRESET_LABELS[id]}
              </button>
            ))}
          </div>

          <div className="mt-3 max-h-40 space-y-1 overflow-y-auto text-xs">
            {loading && <p className="text-slate-400">칼럼 확인 중…</p>}
            {!loading &&
              applicable.map(({ key, header }) => (
                <label key={key} className="flex cursor-pointer items-center gap-2 py-0.5">
                  <input
                    type="checkbox"
                    checked={cols.includes(key)}
                    onChange={() => toggleCol(key)}
                    className="rounded border-slate-300"
                  />
                  <span className={key === "code" ? "text-amber-700" : "text-slate-700"}>{header}</span>
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
