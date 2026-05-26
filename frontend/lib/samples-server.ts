import type { SampleFile } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export async function fetchSamplesServer(): Promise<SampleFile[]> {
  try {
    const r = await fetch(`${API_BASE}/documents/samples`, { cache: "no-store" });
    if (!r.ok) return [];
    return r.json();
  } catch {
    return [];
  }
}
