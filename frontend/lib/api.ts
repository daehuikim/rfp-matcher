export type Judgement = "O" | "△" | "X" | "";

export type Requirement = {
  id: string;
  doc_id: string;
  category: string;
  code: string;
  name: string;
  definition: string | null;
  detail: string;
  deliverables: string | null;
  related: string[];
};

export type CatalogCandidateAudit = {
  catalog_id: string;
  solution_name: string;
  category_major: string;
  similarity_score: number;
  selected: boolean;
  exclusion_reason: string | null;
};

export type Recommendation = {
  requirement_id: string;
  ai_risk: Judgement;
  ai_reason: string;
  missing_tech: string[];
  consortium_need: string | null;
  matched_solutions?: string[];
  catalog_audit?: CatalogCandidateAudit[];
};

export type HumanJudgement = {
  requirement_id: string;
  mark: Judgement;
  note: string;
};

export type RequirementView = {
  requirement: Requirement;
  recommendation: Recommendation | null;
  judgement: HumanJudgement | null;
};

const API = process.env.NEXT_PUBLIC_API_BASE ?? "/api";

export async function listRequirements(docId: string): Promise<RequirementView[]> {
  const r = await fetch(`${API}/documents/${docId}/requirements`, { cache: "no-store" });
  if (!r.ok) throw new Error(`list ${r.status}`);
  return r.json();
}

export async function patchJudgement(
  reqId: string,
  mark: Judgement,
  note: string,
  editorId: string,
): Promise<HumanJudgement> {
  const r = await fetch(`${API}/requirements/${reqId}/judgement`, {
    method: "PATCH",
    headers: { "content-type": "application/json", "x-editor-id": editorId },
    body: JSON.stringify({ mark, note }),
  });
  if (!r.ok) throw new Error(`patch ${r.status}`);
  return r.json();
}

export function eventStreamUrl(docId: string): string {
  return `${API}/documents/${docId}/events`;
}

export type JudgementUpdatedPayload = {
  requirement_id: string;
  mark: Judgement;
  note: string;
  editor_id: string;
  ts: string;
};

export async function uploadDocument(file: File): Promise<{ doc_id: string; status: string }> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API}/documents`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`upload ${r.status}`);
  return r.json();
}

export type SampleFile = {
  name: string;
  size_bytes: number;
  ext: string;
  display: string;
};

export async function listSamples(): Promise<SampleFile[]> {
  const r = await fetch(`${API}/documents/samples`, { cache: "no-store" });
  if (!r.ok) throw new Error(`samples ${r.status}`);
  return r.json();
}

export async function createFromSample(name: string): Promise<{ doc_id: string; status: string }> {
  const r = await fetch(`${API}/documents/from-sample`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) throw new Error(`from-sample ${r.status}`);
  return r.json();
}

export function exportUrl(
  docId: string,
  mode: "ai" | "human" | "both",
  cols?: string[],
): string {
  const params = new URLSearchParams({ mode });
  if (cols?.length) params.set("cols", cols.join(","));
  return `${API}/documents/${docId}/export?${params.toString()}`;
}

export type PipelineStatusResponse = {
  doc_id: string;
  stage: string;
  payload: Record<string, unknown>;
  error?: string | null;
  ts?: string;
};

export async function fetchPipelineStatus(docId: string): Promise<PipelineStatusResponse> {
  const r = await fetch(`${API}/documents/${docId}/pipeline`, { cache: "no-store" });
  if (!r.ok) throw new Error(`pipeline ${r.status}`);
  return r.json();
}
