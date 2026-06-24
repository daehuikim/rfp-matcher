"use client";

import { useEffect, useMemo, useState } from "react";
import { exportUrl, fetchExportColumns, type ExportColumnInfo } from "@/lib/api";

const PRESET_LABELS: Record<string, string> = {
  조견표: "조견표",
  original: "원본만",
  standard: "표준",
  full: "전체",
};

function baseFromName(name?: string | null): string {
  if (!name) return "조견표";
  return name.replace(/\.[^.]+$/, "").trim() || "조견표";
}

export function ExportPanel({
  docId,
  disabled,
  sourceName,
}: {
  docId: string;
  disabled?: boolean;
  sourceName?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"human" | "ai" | "both">("both");
  const [preset, setPreset] = useState("조견표");
  const [applicable, setApplicable] = useState<ExportColumnInfo[]>([]);
  const [cols, setCols] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [filename, setFilename] = useState("");
  const [filenameTouched, setFilenameTouched] = useState(false);

  const defaultName = useMemo(() => {
    const modeTag = mode === "ai" ? "AI" : mode === "human" ? "사람" : "전체";
    return `${baseFromName(sourceName)}_rfp분석_원문순서_${modeTag}`;
  }, [sourceName, mode]);

  const effectiveName = filenameTouched ? filename : defaultName;

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
    () =>
      disabled || cols.length === 0
        ? "#"
        : exportUrl(docId, mode, cols, effectiveName),
    [docId, mode, cols, effectiveName, disabled],
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
            RFP 원문 순서 조견표 (표안표·이미지 포함, 백엔드와 동일 형식)
          </p>

          <p className="mt-3 text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
            칼럼 구성
          </p>
          <p className="mt-1 text-[10px] text-slate-500">
            레거시 문서에만 적용됩니다. V3 샘플은 원문 조견표가 우선 출력됩니다.
          </p>

          <div className="mt-3 flex flex-wrap gap-1">
            {Object.keys(PRESET_LABELS).map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => setPreset(id)}
                className={`pill ${
                  preset === id
                    ? "border-ink-900 bg-ink-900 text-white"
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
                    ? "border-ink-900 bg-ink-900 text-white"
                    : "border-slate-200 bg-white text-slate-600"
                }`}
              >
                {m === "human" ? "사람" : m === "ai" ? "AI" : "둘 다"}
              </button>
            ))}
          </div>

          <p className="mt-3 text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
            파일명
          </p>
          <div className="mt-1 flex items-center gap-1 rounded-lg border border-neutral-200 px-2 py-1.5 focus-within:border-ink-900">
            <input
              type="text"
              value={effectiveName}
              onChange={(e) => {
                setFilenameTouched(true);
                setFilename(e.target.value);
              }}
              placeholder={defaultName}
              className="min-w-0 flex-1 text-[12px] text-neutral-800 outline-none placeholder:text-neutral-400"
            />
            <span className="shrink-0 text-[11px] text-neutral-400">.xlsx</span>
            {filenameTouched && (
              <button
                type="button"
                onClick={() => {
                  setFilenameTouched(false);
                  setFilename("");
                }}
                className="shrink-0 text-[10px] text-neutral-400 hover:text-ink-900"
                title="기본값으로"
              >
                ↺
              </button>
            )}
          </div>

          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            onClick={() => setOpen(false)}
            className="btn-secondary mt-3 w-full text-center"
          >
            다운로드
          </a>
        </div>
      )}
    </div>
  );
}
