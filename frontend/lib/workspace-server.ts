import type { CachedProjectSummary, WorkspaceSessionSummary } from "@/lib/api";

function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
}

export type WorkspaceBootstrap = {
  serverSessions: WorkspaceSessionSummary[];
  cachedProjects: CachedProjectSummary[];
  backendReachable: boolean;
};

export async function fetchWorkspaceBootstrap(): Promise<WorkspaceBootstrap> {
  const base = apiBase();
  try {
    const [sessionsRes, cachedRes] = await Promise.all([
      fetch(`${base}/documents/sessions`, { cache: "no-store" }),
      fetch(`${base}/documents/cached-projects`, { cache: "no-store" }),
    ]);
    const serverSessions: WorkspaceSessionSummary[] = sessionsRes.ok ? await sessionsRes.json() : [];
    const cachedProjects: CachedProjectSummary[] = cachedRes.ok ? await cachedRes.json() : [];
    return {
      serverSessions,
      cachedProjects,
      backendReachable: sessionsRes.ok || cachedRes.ok,
    };
  } catch {
    return { serverSessions: [], cachedProjects: [], backendReachable: false };
  }
}
