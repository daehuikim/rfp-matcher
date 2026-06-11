"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchLlmSettings, patchLlmSettings, type LlmOption, type LlmSettings } from "@/lib/api";

const STORAGE_KEY = "rfp-llm-provider";

function optionProvider(option: LlmOption): string {
  return option.provider;
}

export function useLlmModelSelection() {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchLlmSettings();
      setSettings(next);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_KEY, next.provider);
      }
    } catch {
      const saved = typeof window !== "undefined" ? window.localStorage.getItem(STORAGE_KEY) : null;
      if (saved === "openai" || saved === "gemma") {
        setSettings({
          provider: saved,
          model: saved === "gemma" ? "gemma-4-26B-4aB-it" : "gpt-4o",
          options: [
            { id: "gemma", label: "Gemma 4", provider: "gemma", model: "gemma-4-26B-4aB-it" },
            { id: "gpt-4o", label: "GPT-4o", provider: "openai", model: "gpt-4o" },
          ],
        });
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setProvider = useCallback(
    async (provider: string) => {
      if (busy || settings?.provider === provider) return;
      setBusy(true);
      try {
        const next = await patchLlmSettings(provider);
        setSettings(next);
        window.localStorage.setItem(STORAGE_KEY, next.provider);
      } catch {
        /* backend offline — local choice still used on upload */
        window.localStorage.setItem(STORAGE_KEY, provider);
        setSettings((prev) =>
          prev
            ? {
                ...prev,
                provider,
                model: provider === "gemma" ? "gemma-4-26B-4aB-it" : "gpt-4o",
              }
            : prev,
        );
      } finally {
        setBusy(false);
      }
    },
    [busy, settings?.provider],
  );

  const selectedProvider =
    settings?.provider ??
    (typeof window !== "undefined" ? window.localStorage.getItem(STORAGE_KEY) : null) ??
    "gemma";

  return { settings, selectedProvider, setProvider, busy, refresh };
}

type Props = {
  compact?: boolean;
};

export function LlmModelSelector({ compact = false }: Props) {
  const { settings, selectedProvider, setProvider, busy } = useLlmModelSelection();
  const options = settings?.options ?? [
    { id: "gemma", label: "Gemma 4", provider: "gemma", model: "gemma-4-26B-4aB-it" },
    { id: "gpt-4o", label: "GPT-4o", provider: "openai", model: "gpt-4o" },
  ];

  return (
    <label
      className={`flex items-center gap-2 ${compact ? "text-xs" : "text-sm"}`}
      title="추출·매칭에 사용할 LLM"
    >
      {!compact && <span className="hidden text-neutral-500 sm:inline">LLM</span>}
      <select
        value={selectedProvider}
        disabled={busy}
        onChange={(e) => void setProvider(e.target.value)}
        className="max-w-[9rem] truncate rounded-lg border border-neutral-200 bg-white px-2 py-1.5 text-xs font-medium text-ink-900 shadow-sm transition hover:border-ktteal-300 focus:border-ktteal-400 focus:outline-none disabled:opacity-60"
      >
        {options.map((opt) => (
          <option key={opt.id} value={optionProvider(opt)}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function getStoredLlmProvider(): string {
  if (typeof window === "undefined") return "gemma";
  return window.localStorage.getItem(STORAGE_KEY) ?? "gemma";
}
