import {
  createFromSample,
  fetchCachedProjects,
  fetchDocumentMeta,
  fetchWorkspaceSessions,
  reopenFromCache,
} from "@/lib/api";
import { loadWorkspace, replaceWorkspaceDocId } from "@/lib/workspace";

function normHash(value: string | undefined | null): string | undefined {
  if (!value) return undefined;
  return value.trim().toLowerCase();
}

export type DocumentRouteResult =
  | { action: "ok"; docId: string }
  | { action: "redirect"; docId: string; contentHash?: string }
  | { action: "missing"; docId: string };

async function restartFromSourceFilename(
  docId: string,
  sourceFilename: string,
): Promise<DocumentRouteResult | null> {
  try {
    const { doc_id } = await createFromSample(sourceFilename);
    replaceWorkspaceDocId(docId, doc_id, {
      title: sourceFilename,
      sourceFilename,
    });
    return { action: "redirect", docId: doc_id };
  } catch {
    return null;
  }
}

/** URL docId가 서버 in-memory와 맞는지 확인 — stale이면 live docId·재시작·reopen 반환 */
export async function reconcileDocumentRoute(docId: string): Promise<DocumentRouteResult> {
  try {
    return await _reconcileDocumentRoute(docId);
  } catch {
    return { action: "missing", docId };
  }
}

async function _reconcileDocumentRoute(docId: string): Promise<DocumentRouteResult> {
  const metaOk = await fetchDocumentMeta(docId);
  if (metaOk) {
    return { action: "ok", docId };
  }

  const local = loadWorkspace().sessions.find((s) => s.docId === docId);
  const cachedProjects = await fetchCachedProjects();
  const hash =
    normHash(local?.contentHash) ??
    normHash(
      cachedProjects.find(
        (c) => c.live_doc_id === docId || c.bucket === docId.slice(0, 16),
      )?.content_hash,
    );

  const sessions = await fetchWorkspaceSessions();

  const byPrefix = sessions.find((s) => s.doc_id.slice(0, 16) === docId.slice(0, 16));
  if (byPrefix && byPrefix.doc_id !== docId) {
    replaceWorkspaceDocId(docId, byPrefix.doc_id, {
      contentHash: byPrefix.content_hash ?? hash,
      title: byPrefix.title,
      sourceFilename: byPrefix.source_filename ?? local?.sourceFilename,
    });
    return {
      action: "redirect",
      docId: byPrefix.doc_id,
      contentHash: byPrefix.content_hash ?? hash,
    };
  }

  if (hash) {
    const live = sessions.find((s) => normHash(s.content_hash) === hash);
    if (live) {
      replaceWorkspaceDocId(docId, live.doc_id, {
        contentHash: live.content_hash ?? hash,
        title: live.title,
        sourceFilename: live.source_filename ?? local?.sourceFilename,
      });
      return {
        action: "redirect",
        docId: live.doc_id,
        contentHash: live.content_hash ?? hash,
      };
    }

    const cached = cachedProjects.find((c) => normHash(c.content_hash) === hash);
    if (cached?.live_doc_id) {
      replaceWorkspaceDocId(docId, cached.live_doc_id, {
        contentHash: cached.content_hash,
        title: cached.title,
        sourceFilename: cached.source_name ?? local?.sourceFilename,
      });
      return {
        action: "redirect",
        docId: cached.live_doc_id,
        contentHash: cached.content_hash,
      };
    }

    if (cached) {
      try {
        const { doc_id } = await reopenFromCache(hash);
        replaceWorkspaceDocId(docId, doc_id, {
          contentHash: hash,
          title: local?.title ?? cached.title,
          sourceFilename: cached.source_name ?? local?.sourceFilename,
        });
        return { action: "redirect", docId: doc_id, contentHash: hash };
      } catch {
        /* artifacts 수동 삭제 — 원본 샘플로 새로 시작 */
      }
    }

    const sourceFilename =
      local?.sourceFilename ??
      cached?.source_name ??
      cachedProjects.find((c) => normHash(c.content_hash) === hash)?.source_name;
    if (sourceFilename) {
      const restarted = await restartFromSourceFilename(docId, sourceFilename);
      if (restarted) return restarted;
    }
  }

  if (local?.sourceFilename) {
    const restarted = await restartFromSourceFilename(docId, local.sourceFilename);
    if (restarted) return restarted;
  }

  return { action: "missing", docId };
}
