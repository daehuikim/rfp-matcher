"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  fetchCachedProjects,
  fetchDocumentMeta,
  fetchWorkspaceSessions,
  createFromSample,
  reopenFromCache,
  resetWorkspace,
  type CachedProjectSummary,
  type WorkspaceSessionSummary,
} from "@/lib/api";
import { getStoredLlmProvider } from "@/components/LlmModelSelector";
import {
  clearWorkspace,
  loadWorkspace,
  pruneStaleWorkspaceSessions,
  replaceWorkspaceDocId,
  touchWorkspaceSession,
  updateWorkspaceSessionContentHash,
  upsertWorkspaceSession,
  type WorkspaceSessionEntry,
} from "@/lib/workspace";

function normHash(value: string | undefined | null): string | undefined {
  if (!value) return undefined;
  return value.trim().toLowerCase();
}

export type WorkspaceNavItem = {
  key: string;
  docId: string;
  staleDocId?: string;
  contentHash?: string;
  title: string;
  sourceFilename?: string | null;
  stage: string;
  isComplete: boolean;
  totalElapsedMs: number;
  serverAlive: boolean;
  fromCacheOnly: boolean;
};

type WorkspaceContextValue = {
  sessions: WorkspaceSessionEntry[];
  serverSessions: WorkspaceSessionSummary[];
  cachedProjects: CachedProjectSummary[];
  navReady: boolean;
  backendReachable: boolean;
  registerSession: (docId: string, title: string, contentHash?: string, sourceFilename?: string) => void;
  refreshServerSessions: () => Promise<void>;
  openProject: (item: WorkspaceNavItem) => Promise<void>;
  resetAllProjects: () => Promise<void>;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function mergeTitle(local: WorkspaceSessionEntry, server?: WorkspaceSessionSummary): string {
  return server?.title || local.title;
}

function buildNavItems(
  sessions: WorkspaceSessionEntry[],
  serverSessions: WorkspaceSessionSummary[],
  cachedProjects: CachedProjectSummary[],
): WorkspaceNavItem[] {
  const items: WorkspaceNavItem[] = [];
  const seenDocIds = new Set<string>();
  const seenHashes = new Set<string>();

  // 1) 서버 in-memory 세션 — localStorage 없어도 항상 표시
  for (const server of serverSessions) {
    const hash = normHash(server.content_hash);
    if (hash) seenHashes.add(hash);
    seenDocIds.add(server.doc_id);
    items.push({
      key: `server:${server.doc_id}`,
      docId: server.doc_id,
      contentHash: server.content_hash ?? undefined,
      title: server.title,
      sourceFilename: server.source_filename,
      stage: server.stage,
      isComplete: server.is_complete,
      totalElapsedMs: server.total_elapsed_ms,
      serverAlive: true,
      fromCacheOnly: false,
    });
  }

  const serverByHash = new Map<string, WorkspaceSessionSummary>();
  for (const s of serverSessions) {
    const h = normHash(s.content_hash);
    if (h) serverByHash.set(h, s);
  }

  // 2) artifacts 디스크 캐시 — live 세션에 없는 것
  for (const cached of cachedProjects) {
    const hash = normHash(cached.content_hash);
    const server = hash ? serverByHash.get(hash) : undefined;
    if (server) continue;
    if (cached.live_doc_id && seenDocIds.has(cached.live_doc_id)) continue;
    if (hash && seenHashes.has(hash)) continue;
    if (hash) seenHashes.add(hash);
    const docId = cached.live_doc_id ?? cached.bucket;
    if (seenDocIds.has(docId)) continue;
    seenDocIds.add(docId);
    items.push({
      key: `cache:${cached.bucket}`,
      docId,
      contentHash: cached.content_hash,
      title: cached.title,
      sourceFilename: cached.source_name,
      stage: cached.stage,
      isComplete: cached.is_complete,
      totalElapsedMs: cached.total_elapsed_ms,
      serverAlive: Boolean(cached.live_doc_id),
      fromCacheOnly: !cached.live_doc_id,
    });
  }

  // 3) localStorage only (오프라인·stale)
  for (const local of sessions) {
    if (seenDocIds.has(local.docId)) continue;
    const hash = normHash(local.contentHash);
    if (hash && seenHashes.has(hash)) continue;
    seenDocIds.add(local.docId);
    items.push({
      key: `local:${local.docId}`,
      docId: local.docId,
      contentHash: local.contentHash,
      title: local.title,
      stage: "UPLOADED",
      isComplete: false,
      totalElapsedMs: 0,
      serverAlive: false,
      fromCacheOnly: true,
    });
  }

  return items;
}

export function WorkspaceProvider({
  children,
  initialServerSessions = [],
  initialCachedProjects = [],
  initialBackendReachable = false,
}: {
  children: ReactNode;
  initialServerSessions?: WorkspaceSessionSummary[];
  initialCachedProjects?: CachedProjectSummary[];
  initialBackendReachable?: boolean;
}) {
  const pathname = usePathname();
  const router = useRouter();
  // SSR·hydration 첫 렌더는 동일해야 함 — localStorage는 mount 후 useEffect에서만 읽음
  const [sessions, setSessions] = useState<WorkspaceSessionEntry[]>([]);
  const [serverSessions, setServerSessions] =
    useState<WorkspaceSessionSummary[]>(initialServerSessions);
  const [cachedProjects, setCachedProjects] =
    useState<CachedProjectSummary[]>(initialCachedProjects);
  const [navReady, setNavReady] = useState(
    initialServerSessions.length > 0 || initialCachedProjects.length > 0 || initialBackendReachable,
  );
  const [backendReachable, setBackendReachable] = useState(initialBackendReachable);

  const refreshServerSessions = useCallback(async () => {
    try {
      const [list, cached] = await Promise.all([fetchWorkspaceSessions(), fetchCachedProjects()]);
      setServerSessions(list);
      setCachedProjects(cached);
      setBackendReachable(true);
      setNavReady(true);
      const pruned = pruneStaleWorkspaceSessions(
        loadWorkspace().sessions,
        list.map((s) => s.doc_id),
        cached.map((c) => c.content_hash),
      );
      setSessions(pruned.sessions);
    } catch {
      setBackendReachable(false);
      setNavReady(true);
    }
  }, []);

  const registerSession = useCallback(
    (docId: string, title: string, contentHash?: string, sourceFilename?: string) => {
      setSessions(
        upsertWorkspaceSession({
          docId,
          title,
          openedAt: new Date().toISOString(),
          contentHash,
          sourceFilename,
        }).sessions,
      );
    },
    [],
  );

  const resetAllProjects = useCallback(async () => {
    try {
      await resetWorkspace();
      setBackendReachable(true);
    } catch {
      setBackendReachable(false);
      throw new Error("워크스페이스 초기화 실패 — 백엔드(8000) 확인");
    }
    clearWorkspace();
    setSessions([]);
    setServerSessions([]);
    setCachedProjects([]);
    setNavReady(true);
    router.push("/");
  }, [router]);

  const openProject = useCallback(
    async (item: WorkspaceNavItem) => {
      const live =
        serverSessions.find((s) => s.doc_id === item.docId) ??
        (item.contentHash
          ? serverSessions.find((s) => normHash(s.content_hash) === normHash(item.contentHash))
          : undefined);

      if (live) {
        if (live.doc_id !== item.docId) {
          replaceWorkspaceDocId(item.docId, live.doc_id, {
            contentHash: live.content_hash ?? item.contentHash,
            title: live.title,
          });
          setSessions(loadWorkspace().sessions);
        }
        router.push(`/edit/${live.doc_id}`);
        touchWorkspaceSession(live.doc_id);
        setSessions(loadWorkspace().sessions);
        return;
      }

      const contentHash = item.contentHash;
      if (!contentHash) {
        router.push(`/edit/${item.docId}`);
        return;
      }

      try {
        const { doc_id } = await reopenFromCache(contentHash);
        const staleId = item.staleDocId ?? item.docId;
        setSessions(
          replaceWorkspaceDocId(staleId, doc_id, {
            contentHash,
            title: item.title,
            sourceFilename: item.sourceFilename ?? undefined,
          }).sessions,
        );
        void refreshServerSessions();
        router.push(`/edit/${doc_id}`);
        return;
      } catch {
        /* artifacts 삭제됨 — 원본 샘플로 새 파이프라인 */
      }

      if (item.sourceFilename) {
        const { doc_id } = await createFromSample(item.sourceFilename, getStoredLlmProvider());
        setSessions(
          replaceWorkspaceDocId(item.docId, doc_id, {
            title: item.title,
            sourceFilename: item.sourceFilename,
          }).sessions,
        );
        void refreshServerSessions();
        router.push(`/edit/${doc_id}`);
        return;
      }

      router.push("/?error=cache-missing");
    },
    [router, refreshServerSessions, serverSessions],
  );

  useEffect(() => {
    setSessions(loadWorkspace().sessions);
    void refreshServerSessions();
    const id = window.setInterval(() => void refreshServerSessions(), 5000);
    return () => clearInterval(id);
  }, [refreshServerSessions]);

  // SSR bootstrap → localStorage 초기 시드
  useEffect(() => {
    if (!initialServerSessions.length && !initialCachedProjects.length) return;
    for (const s of initialServerSessions) {
      upsertWorkspaceSession({
        docId: s.doc_id,
        title: s.title,
        openedAt: new Date().toISOString(),
        contentHash: s.content_hash ?? undefined,
        sourceFilename: s.source_filename ?? undefined,
      });
    }
    const liveHashes = new Set(initialServerSessions.map((s) => normHash(s.content_hash)).filter(Boolean));
    for (const c of initialCachedProjects) {
      const h = normHash(c.content_hash);
      if (h && liveHashes.has(h)) continue;
      upsertWorkspaceSession({
        docId: c.live_doc_id ?? c.bucket,
        title: c.title,
        openedAt: new Date().toISOString(),
        contentHash: c.content_hash,
        sourceFilename: c.source_name ?? undefined,
      });
    }
    setSessions(loadWorkspace().sessions);
  }, [initialServerSessions, initialCachedProjects]);

  // 서버·캐시 목록 → localStorage 동기화 (새로고침 후에도 사이드바 유지)
  useEffect(() => {
    if (!serverSessions.length && !cachedProjects.length) return;
    for (const s of serverSessions) {
      upsertWorkspaceSession({
        docId: s.doc_id,
        title: s.title,
        openedAt: new Date().toISOString(),
        contentHash: s.content_hash ?? undefined,
        sourceFilename: s.source_filename ?? undefined,
      });
    }
    const liveHashes = new Set(serverSessions.map((s) => normHash(s.content_hash)).filter(Boolean));
    for (const c of cachedProjects) {
      const h = normHash(c.content_hash);
      if (h && liveHashes.has(h)) continue;
      upsertWorkspaceSession({
        docId: c.live_doc_id ?? c.bucket,
        title: c.title,
        openedAt: new Date().toISOString(),
        contentHash: c.content_hash,
        sourceFilename: c.source_name ?? undefined,
      });
    }
    setSessions(loadWorkspace().sessions);
  }, [serverSessions, cachedProjects]);

  // content_hash 기준 stale docId → 서버 live docId 자동 교정
  useEffect(() => {
    if (!serverSessions.length) return;
    const byHash = new Map<string, WorkspaceSessionSummary>();
    for (const s of serverSessions) {
      const h = normHash(s.content_hash);
      if (h) byHash.set(h, s);
    }
    let changed = false;
    for (const local of loadWorkspace().sessions) {
      const h = normHash(local.contentHash);
      if (!h) continue;
      const live = byHash.get(h);
      if (live && live.doc_id !== local.docId) {
        replaceWorkspaceDocId(local.docId, live.doc_id, {
          contentHash: h,
          title: live.title,
        });
        changed = true;
      }
    }
    if (changed) setSessions(loadWorkspace().sessions);
  }, [serverSessions]);

  useEffect(() => {
    const m = pathname.match(/^\/(?:review|edit)\/([^/]+)/);
    if (!m) return;
    const docId = m[1];
    const server = serverSessions.find((s) => s.doc_id === docId);
    const cached = cachedProjects.find(
      (c) => c.live_doc_id === docId || normHash(c.content_hash) === normHash(server?.content_hash),
    );
    const title =
      server?.title ??
      cached?.title ??
      server?.display_name ??
      `프로젝트 ${docId.slice(0, 8)}`;
    const contentHash = server?.content_hash ?? cached?.content_hash;
    const local = loadWorkspace().sessions.find((s) => s.docId === docId);
    const sourceFilename =
      server?.source_filename ?? cached?.source_name ?? local?.sourceFilename ?? undefined;
    registerSession(docId, title, contentHash ?? undefined, sourceFilename);
    touchWorkspaceSession(docId);
    setSessions(loadWorkspace().sessions);

    if (!contentHash) {
      void fetchDocumentMeta(docId)
        .then((meta) => {
          if (meta?.content_hash) {
            setSessions(updateWorkspaceSessionContentHash(docId, meta.content_hash).sessions);
          }
        })
        .catch(() => {
          /* stale doc — reconcile on review page */
        });
    }
  }, [pathname, serverSessions, cachedProjects, registerSession]);

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      sessions,
      serverSessions,
      cachedProjects,
      navReady,
      backendReachable,
      registerSession,
      refreshServerSessions,
      openProject,
      resetAllProjects,
    }),
    [
      sessions,
      serverSessions,
      cachedProjects,
      navReady,
      backendReachable,
      registerSession,
      refreshServerSessions,
      openProject,
      resetAllProjects,
    ],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return ctx;
}

/** 사이드바용 — 서버 live + artifacts + localStorage 병합 */
export function useWorkspaceNavItems(): WorkspaceNavItem[] {
  const { sessions, serverSessions, cachedProjects } = useWorkspace();
  return useMemo(
    () => buildNavItems(sessions, serverSessions, cachedProjects),
    [sessions, serverSessions, cachedProjects],
  );
}

export function useActiveProjectKey(pathname: string): string | null {
  const m = pathname.match(/^\/(?:review|edit)\/([^/]+)/);
  if (!m) return null;
  return m[1];
}

export function useActiveProjectNavItem(): WorkspaceNavItem | null {
  const pathname = usePathname();
  const activeDocId = useActiveProjectKey(pathname);
  const items = useWorkspaceNavItems();
  if (!activeDocId) return null;
  return (
    items.find((p) => p.docId === activeDocId || p.staleDocId === activeDocId) ?? null
  );
}
