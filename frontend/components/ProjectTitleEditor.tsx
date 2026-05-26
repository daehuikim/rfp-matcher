"use client";

import { useEffect, useState } from "react";
import { fetchDocumentMeta, patchDocumentMeta } from "@/lib/api";
import { upsertWorkspaceSession } from "@/lib/workspace";

type Props = {
  docId: string;
  initialTitle?: string;
};

export function ProjectTitleEditor({ docId, initialTitle }: Props) {
  const [title, setTitle] = useState(initialTitle ?? "");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [sourceFile, setSourceFile] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (initialTitle) setTitle(initialTitle);
  }, [initialTitle, docId]);

  useEffect(() => {
    void fetchDocumentMeta(docId).then((meta) => {
      if (!meta) return;
      setTitle(meta.title);
      setSourceFile(meta.source_filename);
      upsertWorkspaceSession({
        docId,
        title: meta.title,
        openedAt: new Date().toISOString(),
        contentHash: meta.content_hash ?? undefined,
      });
    });
  }, [docId]);

  async function save(next: string) {
    const trimmed = next.trim();
    if (!trimmed || trimmed === title) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      const meta = await patchDocumentMeta(docId, trimmed);
      setTitle(meta.title);
      upsertWorkspaceSession({ docId, title: meta.title, openedAt: new Date().toISOString() });
      setEditing(false);
    } catch {
      /* keep draft */
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-wider text-slate-400">프로젝트</p>
      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => void save(draft)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void save(draft);
            if (e.key === "Escape") setEditing(false);
          }}
          className="mt-0.5 w-full max-w-md rounded-lg border border-violet-200 bg-white px-2 py-1 text-lg font-bold text-slate-900 outline-none ring-violet-200 focus:ring-2"
          disabled={saving}
        />
      ) : (
        <button
          type="button"
          onClick={() => {
            setDraft(title);
            setEditing(true);
          }}
          className="mt-0.5 block max-w-full truncate text-left text-xl font-bold tracking-tight text-slate-900 hover:text-violet-800"
          title="클릭하여 프로젝트명 변경"
        >
          {title || initialTitle || "프로젝트 불러오는 중…"}
        </button>
      )}
      {sourceFile && (
        <p className="mt-0.5 truncate text-xs text-slate-500" title={sourceFile}>
          원본: {sourceFile}
        </p>
      )}
      <p className="mt-0.5 font-mono text-[10px] text-slate-400">{docId.slice(0, 16)}…</p>
    </div>
  );
}
