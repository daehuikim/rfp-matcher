import type { ExtractionProfile, PipelineStatusResponse, RequirementView } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export async function fetchRequirementsServer(docId: string): Promise<RequirementView[]> {
  try {
    const r = await fetch(`${API_BASE}/documents/${docId}/requirements`, { cache: "no-store" });
    if (!r.ok) return [];
    return r.json();
  } catch {
    return [];
  }
}

export async function fetchPipelineStatusServer(docId: string): Promise<PipelineStatusResponse | null> {
  try {
    const r = await fetch(`${API_BASE}/documents/${docId}/pipeline`, { cache: "no-store" });
    if (!r.ok) return null;
    return r.json();
  } catch {
    return null;
  }
}

export async function fetchExtractionProfileServer(docId: string): Promise<ExtractionProfile | null> {
  try {
    const r = await fetch(`${API_BASE}/documents/${docId}/extraction-profile`, { cache: "no-store" });
    if (!r.ok) return null;
    return r.json();
  } catch {
    return null;
  }
}
