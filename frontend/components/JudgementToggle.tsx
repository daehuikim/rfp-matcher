"use client";

import { Judgement } from "@/lib/api";

const MARKS: { value: Judgement; label: string; base: string; active: string; title: string }[] = [
  { value: "O", label: "O", base: "judge-O", active: "judge-O-active", title: "가능 (O)" },
  { value: "△", label: "△", base: "judge-tri", active: "judge-tri-active", title: "조건부 (△)" },
  { value: "X", label: "X", base: "judge-X", active: "judge-X-active", title: "불가 (X)" },
];

export function JudgementToggle({
  value,
  onChange,
  variant = "circle",
  size = "default",
}: {
  value: Judgement;
  onChange: (next: Judgement) => void;
  variant?: "circle" | "square";
  size?: "default" | "compact";
}) {
  const shape =
    size === "compact"
      ? "judge-btn-compact"
      : variant === "square"
        ? "judge-btn-square"
        : "judge-btn";
  return (
    <div className="inline-flex gap-1.5" role="radiogroup" aria-label="Human Decision">
      {MARKS.map((m) => {
        const selected = value === m.value;
        return (
          <button
            type="button"
            key={m.value}
            role="radio"
            aria-checked={selected}
            title={m.title}
            onClick={() => onChange(selected ? "" : m.value)}
            className={`${shape} ${selected ? m.active : m.base + " bg-white/60 hover:bg-white"}`}
          >
            {m.label}
          </button>
        );
      })}
    </div>
  );
}
