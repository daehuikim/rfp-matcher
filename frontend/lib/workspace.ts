/** 브라우저 localStorage — 열린 프로젝트(세션) 목록 */

export type WorkspaceSessionEntry = {
  docId: string;
  title: string;
  openedAt: string;
  contentHash?: string;
  sourceFilename?: string;
  pinned?: boolean;
};

export type WorkspaceState = {
  sessions: WorkspaceSessionEntry[];
  version: 1;
};

const STORAGE_KEY = "rfp-matcher-workspace-v1";

export function loadWorkspace(): WorkspaceState {
  if (typeof window === "undefined") return { sessions: [], version: 1 };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { sessions: [], version: 1 };
    const parsed = JSON.parse(raw) as WorkspaceState;
    if (!parsed.sessions) return { sessions: [], version: 1 };
    return { sessions: parsed.sessions, version: 1 };
  } catch {
    return { sessions: [], version: 1 };
  }
}

export function saveWorkspace(state: WorkspaceState): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

export function upsertWorkspaceSession(entry: WorkspaceSessionEntry): WorkspaceState {
  const state = loadWorkspace();
  const next = state.sessions.filter((s) => s.docId !== entry.docId);
  next.unshift({ ...entry, openedAt: entry.openedAt || new Date().toISOString() });
  const trimmed = next.slice(0, 24);
  const out = { sessions: trimmed, version: 1 as const };
  saveWorkspace(out);
  return out;
}

export function removeWorkspaceSession(docId: string): WorkspaceState {
  const state = loadWorkspace();
  const out = {
    sessions: state.sessions.filter((s) => s.docId !== docId),
    version: 1 as const,
  };
  saveWorkspace(out);
  return out;
}

export function touchWorkspaceSession(docId: string): WorkspaceState {
  const state = loadWorkspace();
  const idx = state.sessions.findIndex((s) => s.docId === docId);
  if (idx < 0) return state;
  const [item] = state.sessions.splice(idx, 1);
  item.openedAt = new Date().toISOString();
  state.sessions.unshift(item);
  saveWorkspace(state);
  return state;
}

export function updateWorkspaceSessionContentHash(docId: string, contentHash: string): WorkspaceState {
  const state = loadWorkspace();
  const idx = state.sessions.findIndex((s) => s.docId === docId);
  if (idx < 0) return state;
  state.sessions[idx] = { ...state.sessions[idx], contentHash };
  saveWorkspace(state);
  return state;
}

export function replaceWorkspaceDocId(
  oldDocId: string,
  newDocId: string,
  opts?: { contentHash?: string; title?: string; sourceFilename?: string },
): WorkspaceState {
  const state = loadWorkspace();
  const existing = state.sessions.find((s) => s.docId === oldDocId);
  const next = state.sessions.filter((s) => s.docId !== oldDocId && s.docId !== newDocId);
  next.unshift({
    docId: newDocId,
    title: opts?.title ?? existing?.title ?? `프로젝트 ${newDocId.slice(0, 8)}`,
    openedAt: new Date().toISOString(),
    contentHash: opts?.contentHash ?? existing?.contentHash,
    sourceFilename: opts?.sourceFilename ?? existing?.sourceFilename,
    pinned: existing?.pinned,
  });
  const out = { sessions: next.slice(0, 24), version: 1 as const };
  saveWorkspace(out);
  return out;
}

/** 서버·artifacts에 없는 localStorage-only 유령 세션 제거 */
export function pruneStaleWorkspaceSessions(
  sessions: WorkspaceSessionEntry[],
  liveDocIds: string[],
  cachedHashes: string[],
): WorkspaceState {
  const live = new Set(liveDocIds);
  const hashes = new Set(cachedHashes.map((h) => h.trim().toLowerCase()).filter(Boolean));
  const kept = sessions.filter((s) => {
    if (live.has(s.docId)) return true;
    const h = s.contentHash?.trim().toLowerCase();
    if (h && hashes.has(h)) return true;
    return false;
  });
  const out = { sessions: kept, version: 1 as const };
  saveWorkspace(out);
  return out;
}
