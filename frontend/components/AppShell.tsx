"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  useActiveProjectKey,
  useActiveProjectNavItem,
  useWorkspace,
  useWorkspaceNavItems,
  type WorkspaceNavItem,
} from "@/context/WorkspaceProvider";
import { formatDuration } from "@/lib/pipeline";
import { STAGE_LABEL } from "@/lib/pipeline";

const PRIMARY_NAV = [{ href: "/", label: "새 프로젝트", icon: "+" }] as const;

function stageBadge(stage: string, isComplete: boolean): string {
  if (isComplete) return "완료";
  return STAGE_LABEL[stage] ?? stage;
}

function ProjectNavButton({ item, active }: { item: WorkspaceNavItem; active: boolean }) {
  const { openProject } = useWorkspace();
  const [busy, setBusy] = useState(false);

  async function onClick() {
    if (busy) return;
    setBusy(true);
    try {
      await openProject(item);
    } catch {
      /* reopen failed — user can retry from home */
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={() => void onClick()}
      disabled={busy}
      className={`block w-full rounded-lg px-3 py-2.5 text-left text-sm transition-colors disabled:opacity-60 ${
        active
          ? "bg-violet-50 text-violet-900 ring-1 ring-violet-200"
          : "text-slate-700 hover:bg-slate-50"
      }`}
    >
      <div className="truncate font-medium">{item.title}</div>
      {item.sourceFilename && (
        <div className="truncate text-[10px] text-slate-400" title={item.sourceFilename}>
          {item.sourceFilename}
        </div>
      )}
      <div className="mt-0.5 flex items-center justify-between gap-2 text-[10px] text-slate-500">
        <span className="truncate">
          {busy ? "불러오는 중…" : stageBadge(item.stage, item.isComplete)}
          {item.fromCacheOnly && !busy && " · 캐시"}
        </span>
        {item.totalElapsedMs > 0 && (
          <span className="shrink-0 font-mono tabular-nums">
            {formatDuration(item.totalElapsedMs)}
          </span>
        )}
      </div>
    </button>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const activeDocId = useActiveProjectKey(pathname);
  const activeProject = useActiveProjectNavItem();
  const { navReady, backendReachable } = useWorkspace();
  const projects = useWorkspaceNavItems();

  return (
    <div className="app-shell flex min-h-screen">
      <aside className="sidebar hidden w-60 shrink-0 flex-col border-r border-slate-200/80 bg-white md:flex">
        <div className="flex items-center gap-2.5 border-b border-slate-100 px-5 py-5">
          <span className="brand-mark grid h-9 w-9 place-items-center rounded-lg text-sm font-bold text-white">
            R
          </span>
          <div>
            <div className="text-sm font-semibold text-slate-900">RFP Matcher</div>
            <div className="text-[10px] text-slate-500">조견표 · AI 추천</div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto p-3">
          <p className="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            시작
          </p>
          {PRIMARY_NAV.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`nav-link mb-3 flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm ${
                  active ? "nav-link-active" : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                <span className="grid h-7 w-7 place-items-center rounded-md bg-slate-100 text-xs font-semibold">
                  {item.icon}
                </span>
                {item.label}
              </Link>
            );
          })}

          <p className="mb-1.5 mt-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            프로젝트
          </p>
          {projects.length === 0 ? (
            <p className="px-3 py-2 text-xs leading-relaxed text-slate-400">
              {!navReady
                ? "프로젝트 불러오는 중…"
                : !backendReachable
                  ? "백엔드(8000) 미연결 — uvicorn 실행 후 새로고침"
                  : "열린 프로젝트 없음 — 홈에서 샘플 시작"}
            </p>
          ) : (
            <ul className="space-y-0.5">
              {projects.map((p) => {
                const active =
                  activeDocId != null &&
                  (p.docId === activeDocId || p.staleDocId === activeDocId);
                return (
                  <li key={p.key}>
                    <ProjectNavButton item={p} active={active} />
                  </li>
                );
              })}
            </ul>
          )}
        </nav>

        <div className="border-t border-slate-100 p-4 text-[10px] leading-relaxed text-slate-400">
          백그라운드에서 AI·추출이 계속됩니다.
          <br />
          artifacts 캐시 프로젝트도 사이드바에서 재오픈.
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="topbar flex items-center justify-between border-b border-slate-200/80 bg-white/90 px-4 py-3 backdrop-blur md:px-6">
          <div className="flex items-center gap-2 md:hidden">
            <span className="brand-mark grid h-8 w-8 place-items-center rounded-lg text-xs font-bold text-white">
              R
            </span>
            <span className="text-sm font-semibold">RFP Matcher</span>
          </div>
          <div className="hidden min-w-0 md:block">
            {activeProject ? (
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-slate-900">{activeProject.title}</p>
                {activeProject.sourceFilename && (
                  <p className="truncate text-[10px] text-slate-500">{activeProject.sourceFilename}</p>
                )}
              </div>
            ) : (
              <div className="text-xs text-slate-500">
                RFP/RFI 조견표 자동 추출 · O/△/X AI 리스크
              </div>
            )}
          </div>
          <span className="status-pill">
            <span className="status-dot" />
            Live
          </span>
        </header>
        <main className="main-content flex-1 px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
