import type { PipelineEventData } from "@/lib/pipeline";

export type Judgement = "O" | "△" | "X" | "";

export type CategorySource = "document_table" | "section_heading" | "system_inferred";

export type Requirement = {
  id: string;
  doc_id: string;
  category: string;
  subcategory?: string | null;
  category_source?: CategorySource;
  subcategory_source?: CategorySource | null;
  code: string;
  name: string;
  definition: string | null;
  detail: string;
  deliverables: string | null;
  related: string[];
  source_page?: number | string | null;
  source_section?: string | null;
  source_atomic_id?: string | null;
  source_table_index?: number | null;
  source_ref?: string | null;
  detail_images?: string[];
};

export type CatalogCandidateAudit = {
  catalog_id: string;
  solution_name: string;
  sku_label?: string;
  category_major: string;
  category_mid?: string;
  category_sub?: string;
  description?: string;
  similarity_score: number;
  selected: boolean;
  exclusion_reason: string | null;
};

export type MatchedSolutionSku = {
  catalog_id: string;
  solution_name: string;
  sku_label: string;
  category_major: string;
  category_mid: string;
  category_sub: string;
  description: string;
};

export type Recommendation = {
  requirement_id: string;
  ai_risk: Judgement;
  ai_reason: string;
  related_solution?: string;
  missing_tech: string[];
  consortium_need: string | null;
  matched_solutions?: string[];
  matched_solution_skus?: MatchedSolutionSku[];
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

export type ExtractionProfile = {
  spec: string;
  has_requirement_category_column: boolean;
  atomization_strategy: string;
  category_column_header: string | null;
  has_inferred_categories: boolean;
  category_source_counts: Record<string, number>;
};

export function categorySourceLabel(source?: CategorySource | null): string {
  switch (source) {
    case "document_table":
      return "원문 조견표";
    case "section_heading":
      return "섹션 구조";
    case "system_inferred":
      return "시스템 추론";
    default:
      return "원문 조견표";
  }
}

/** 브라우저는 Next rewrite(/api)만 사용 — 직접 8000 호출 시 CORS·fetch 실패 방지 */
function apiBase(): string {
  if (typeof window !== "undefined") return "/api";
  return process.env.NEXT_PUBLIC_API_BASE ?? "/api";
}

/**
 * 브라우저가 직접 소비하는 URL(anchor href·img/iframe src·EventSource) 전용 base.
 * apiBase() 는 SSR fetch 를 위해 서버에서 절대주소를 돌려주지만, href/src 는 SSR·client
 * 가 동일 문자열이어야 하이드레이션 불일치가 없다. 브라우저는 항상 /api(rewrite)만 쓰므로 고정.
 */
function browserBase(): string {
  return "/api";
}

export async function fetchExtractionProfile(docId: string): Promise<ExtractionProfile | null> {
  const r = await fetch(`${apiBase()}/documents/${docId}/extraction-profile`, { cache: "no-store" });
  if (!r.ok) return null;
  return r.json();
}

export async function listRequirements(docId: string): Promise<RequirementView[]> {
  const r = await fetch(`${apiBase()}/documents/${docId}/requirements`, { cache: "no-store" });
  if (!r.ok) throw new Error(`list ${r.status}`);
  return r.json();
}

export async function patchJudgement(
  reqId: string,
  mark: Judgement,
  note: string,
  editorId: string,
): Promise<HumanJudgement> {
  const r = await fetch(`${apiBase()}/requirements/${reqId}/judgement`, {
    method: "PATCH",
    headers: { "content-type": "application/json", "x-editor-id": editorId },
    body: JSON.stringify({ mark, note }),
  });
  if (!r.ok) throw new Error(`patch ${r.status}`);
  return r.json();
}

export function eventStreamUrl(docId: string): string {
  return `${browserBase()}/documents/${docId}/events`;
}

export type JudgementUpdatedPayload = {
  requirement_id: string;
  mark: Judgement;
  note: string;
  editor_id: string;
  ts: string;
};

export async function uploadDocument(
  file: File,
  llmProvider?: string,
  engine?: string,
): Promise<{ doc_id: string; status: string }> {
  const fd = new FormData();
  fd.append("file", file);
  if (llmProvider) fd.append("llm_provider", llmProvider);
  if (engine) fd.append("engine", engine); // 'v_rule' 이면 룰 엔진, 미지정=기본 v2
  const r = await fetch(`${apiBase()}/documents`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`upload ${r.status}`);
  return r.json();
}

/** 조견표 Excel(.xlsx) 업로드 — 편집본 재업로드/외부 조견표 불러오기. */
export async function importExcel(
  file: File,
  llmProvider?: string,
): Promise<{ doc_id: string; status: string }> {
  const fd = new FormData();
  fd.append("file", file);
  if (llmProvider) fd.append("llm_provider", llmProvider);
  const r = await fetch(`${apiBase()}/documents/import-excel`, { method: "POST", body: fd });
  if (!r.ok) {
    let msg = `import ${r.status}`;
    try {
      const j = await r.json();
      if (j?.detail) msg = String(j.detail);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return r.json();
}

export type SampleFile = {
  name: string;
  size_bytes: number;
  ext: string;
  display: string;
  featured?: boolean;
};

export async function listSamples(): Promise<SampleFile[]> {
  const r = await fetch(`${apiBase()}/documents/samples`, { cache: "no-store" });
  if (!r.ok) throw new Error(`samples ${r.status}`);
  return r.json();
}

export async function createFromSample(
  name: string,
  llmProvider?: string,
): Promise<{ doc_id: string; status: string }> {
  const r = await fetch(`${apiBase()}/documents/from-sample`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, llm_provider: llmProvider ?? null }),
  });
  if (!r.ok) throw new Error(`from-sample ${r.status}`);
  return r.json();
}

export type LlmOption = {
  id: string;
  label: string;
  provider: string;
  model: string;
};

export type LlmSettings = {
  provider: string;
  model: string;
  options: LlmOption[];
};

export async function fetchLlmSettings(): Promise<LlmSettings> {
  const r = await fetch(`${apiBase()}/settings/llm`, { cache: "no-store" });
  if (!r.ok) throw new Error(`llm-settings ${r.status}`);
  return r.json();
}

export async function patchLlmSettings(provider: string): Promise<LlmSettings> {
  const r = await fetch(`${apiBase()}/settings/llm`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  if (!r.ok) throw new Error(`llm-settings patch ${r.status}`);
  return r.json();
}

export function exportUrl(
  docId: string,
  mode: "ai" | "human" | "both",
  cols?: string[],
  filename?: string,
): string {
  const params = new URLSearchParams({ mode, layout: "ordered" });
  if (cols?.length) params.set("cols", cols.join(","));
  else params.set("adaptive", "true");
  if (filename && filename.trim()) params.set("filename", filename.trim());
  return `${browserBase()}/documents/${docId}/export?${params.toString()}`;
}

/** 요건 첨부 PNG — [표]·관련 화면(안) */
export function assetUrl(docId: string, relPath: string): string {
  const params = new URLSearchParams({ path: relPath });
  return `${browserBase()}/documents/${docId}/asset?${params.toString()}`;
}

export type ExportColumnInfo = {
  key: string;
  header: string;
  group: string;
};

export type ExportColumnsResponse = {
  preset: string;
  mode: string;
  selected: string[];
  applicable: ExportColumnInfo[];
  presets: Record<string, string[]>;
};

export async function fetchExportColumns(
  docId: string,
  mode: "ai" | "human" | "both" = "both",
  preset = "standard",
): Promise<ExportColumnsResponse> {
  const params = new URLSearchParams({ mode, preset });
  const r = await fetch(`${apiBase()}/documents/${docId}/export/columns?${params.toString()}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`export-columns ${r.status}`);
  return r.json();
}

export type PipelineStatusResponse = {
  doc_id: string;
  stage: string;
  payload: Record<string, unknown>;
  history?: PipelineHistoryEntry[];
  error?: string | null;
  ts?: string;
  llm_provider?: string;
  llm_model?: string;
  llm_usage?: LlmUsage;
  timing_summary?: {
    total_elapsed_ms: number;
    from_cache?: boolean;
    recorded_total_ms?: number | null;
  };
};

export type WorkspaceSessionSummary = {
  doc_id: string;
  title: string;
  source_filename: string | null;
  display_name: string | null;
  content_hash?: string | null;
  stage: string;
  requirements_count: number;
  ai_done: number;
  ai_total: number;
  total_elapsed_ms: number;
  is_complete: boolean;
  updated_at: string | null;
};

export type CachedProjectSummary = {
  content_hash: string;
  bucket: string;
  source_name: string | null;
  title: string;
  requirements_count: number;
  recommendation_count: number;
  has_recommendations: boolean;
  total_elapsed_ms: number;
  stage: string;
  is_complete: boolean;
  live_doc_id: string | null;
  is_live: boolean;
};

export type DocumentMeta = {
  doc_id: string;
  title: string;
  source_filename: string | null;
  display_name: string | null;
  content_hash?: string | null;
  mime?: string | null;
  has_source_file?: boolean;
  has_preview?: boolean;
  is_pdf?: boolean;
  preview_kind?: "pdf" | "html" | "none";
};

/** 원본 파일 그대로(다운로드·새 탭용). */
export function documentSourceUrl(docId: string): string {
  return `${browserBase()}/documents/${docId}/source`;
}

/**
 * 우측 뷰어용 미리보기 URL — 어떤 포맷이든 표시 가능.
 * PDF는 원본 그대로(#page=N 페이지 이동), DOC/DOCX 등은 PDF 변환,
 * 변환 불가(HWPX 등)는 변환 HTML 로 폴백된다.
 */
export function documentPreviewUrl(docId: string): string {
  return `${browserBase()}/documents/${docId}/preview`;
}

export type RfpOverview = {
  available: boolean;
  summary?: string;
  techs?: string[][]; // [기술, 요구, 관련 ID]
  risks?: string[][]; // [내용/ID ...]
};

export async function fetchOverview(docId: string): Promise<RfpOverview | null> {
  const r = await fetch(`${apiBase()}/documents/${docId}/overview`, { cache: "no-store" });
  if (!r.ok) return null;
  return r.json();
}

export async function fetchDocumentMeta(docId: string): Promise<DocumentMeta | null> {
  const r = await fetch(`${apiBase()}/documents/${docId}/meta`, { cache: "no-store" });
  if (!r.ok) return null;
  return r.json();
}

export async function patchDocumentMeta(docId: string, displayName: string): Promise<DocumentMeta> {
  const r = await fetch(`${apiBase()}/documents/${docId}/meta`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ display_name: displayName }),
  });
  if (!r.ok) throw new Error(`patch meta ${r.status}`);
  return r.json();
}

export async function fetchWorkspaceSessions(): Promise<WorkspaceSessionSummary[]> {
  const r = await fetch(`${apiBase()}/documents/sessions`, { cache: "no-store" });
  if (!r.ok) return [];
  return r.json();
}

export async function fetchCachedProjects(): Promise<CachedProjectSummary[]> {
  const r = await fetch(`${apiBase()}/documents/cached-projects`, { cache: "no-store" });
  if (!r.ok) return [];
  return r.json();
}

export async function resetWorkspace(): Promise<{
  ok: boolean;
  storage_entries_removed: number;
  artifact_buckets_removed: number;
}> {
  const r = await fetch(`${apiBase()}/documents/workspace/reset`, { method: "POST" });
  if (!r.ok) throw new Error(`workspace reset ${r.status}`);
  return r.json();
}

export async function reopenFromCache(contentHash: string): Promise<{ doc_id: string; status: string }> {
  const r = await fetch(`${apiBase()}/documents/reopen-cache`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ content_hash: contentHash }),
  });
  if (!r.ok) throw new Error(`reopen-cache ${r.status}`);
  return r.json();
}

export type PipelineHistoryEntry = {
  stage: string;
  payload: PipelineEventData["payload"];
  ts: string;
};

export async function fetchLlmUsage(docId: string): Promise<LlmUsage> {
  const r = await fetch(`${apiBase()}/documents/${docId}/llm-usage`, { cache: "no-store" });
  if (!r.ok) throw new Error(`llm-usage ${r.status}`);
  return r.json();
}

export type LlmCallRecord = {
  purpose: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  prompt_preview: string;
};

export type LlmUsage = {
  provider: string;
  model: string;
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  total_cost_krw: number;
  recent_calls: LlmCallRecord[];
};

export async function fetchPipelineStatus(docId: string): Promise<PipelineStatusResponse> {
  const r = await fetch(`${apiBase()}/documents/${docId}/pipeline`, { cache: "no-store" });
  if (!r.ok) throw new Error(`pipeline ${r.status}`);
  return r.json();
}

export async function ensurePipeline(
  docId: string,
): Promise<{ status: string; reason?: string | null }> {
  const r = await fetch(`${apiBase()}/documents/${docId}/ensure-pipeline`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(`ensure-pipeline ${r.status}`);
  return r.json();
}

// ── FE 편집기능(병합/삭제/편집) — BE row-ops 엔드포인트 호출 ──
export async function editRequirement(
  reqId: string,
  fields: { name?: string; definition?: string; detail?: string; code?: string },
  editorId?: string,
): Promise<Requirement> {
  const r = await fetch(`${apiBase()}/requirements/${reqId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-Editor-Id": editorId ?? "" },
    body: JSON.stringify(fields),
  });
  if (!r.ok) throw new Error(`edit ${r.status}`);
  return r.json();
}

export async function deleteRequirement(docId: string, reqId: string): Promise<RequirementView[]> {
  const r = await fetch(`${apiBase()}/documents/${docId}/requirements/${reqId}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`delete ${r.status}`);
  return r.json();
}

/** 카드(탭) 통째 삭제 — 여러 행 일괄 삭제 후 한 번만 재정렬. */
export async function deleteRequirementsBatch(docId: string, reqIds: string[]): Promise<RequirementView[]> {
  const r = await fetch(`${apiBase()}/documents/${docId}/requirements/delete-batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ req_ids: reqIds }),
  });
  if (!r.ok) throw new Error(`delete-batch ${r.status}`);
  return r.json();
}

export async function mergeRequirements(
  docId: string,
  reqId: string,
  withId: string,
): Promise<RequirementView[]> {
  const r = await fetch(`${apiBase()}/documents/${docId}/requirements/${reqId}/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ with_id: withId }),
  });
  if (!r.ok) throw new Error(`merge ${r.status}`);
  return r.json();
}

export function exportFixedUrl(docId: string): string {
  return `${browserBase()}/documents/${docId}/export-fixed`;
}

/** 분해 — 상세내용을 사용자 지정 기호로 여러 행으로 쪼갬(병합의 반대). */
export async function splitRequirement(
  docId: string,
  reqId: string,
  delimiter: string,
): Promise<RequirementView[]> {
  const r = await fetch(`${apiBase()}/documents/${docId}/requirements/${reqId}/split`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ delimiter }),
  });
  if (!r.ok) {
    let m = `split ${r.status}`;
    try { const j = await r.json(); if (j?.detail) m = String(j.detail); } catch { /* */ }
    throw new Error(m);
  }
  return r.json();
}

/** 카드 병합 / ID 일괄지정 — 지정 행들에 같은 탭(카드)·같은 ID 접두사 적용. */
export async function regroupRequirements(
  docId: string,
  reqIds: string[],
  opts: { prefix?: string; category?: string; name?: string },
): Promise<RequirementView[]> {
  const r = await fetch(`${apiBase()}/documents/${docId}/requirements/regroup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ req_ids: reqIds, prefix: opts.prefix ?? null, category: opts.category ?? null, name: opts.name ?? null }),
  });
  if (!r.ok) throw new Error(`regroup ${r.status}`);
  return r.json();
}
