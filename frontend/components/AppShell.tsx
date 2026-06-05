"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
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
      className={`block w-full rounded-md px-2.5 py-1.5 text-left text-[13px] transition-colors disabled:opacity-60 ${
        active
          ? "bg-neutral-100 text-ink-900"
          : "text-neutral-700 hover:bg-neutral-50"
      }`}
    >
      <div className="truncate font-medium leading-tight">{item.title}</div>
      <div className="mt-0.5 flex items-center justify-between gap-2 text-[10px] text-neutral-400">
        <span className="truncate">
          {busy ? "불러오는 중…" : stageBadge(item.stage, item.isComplete)}
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

function HamburgerIcon({ open }: { open: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      {open ? (
        <>
          <path d="M4 4l10 10M14 4L4 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </>
      ) : (
        <>
          <path d="M2.5 4.5h13M2.5 9h13M2.5 13.5h13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </>
      )}
    </svg>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const activeDocId = useActiveProjectKey(pathname);
  const activeProject = useActiveProjectNavItem();
  const { navReady, backendReachable } = useWorkspace();
  const projects = useWorkspaceNavItems();

  // 사이드바는 기본 접힘 — 조견표가 메인이 되도록
  const [sidebarOpen, setSidebarOpen] = useState(false);
  useEffect(() => {
    const saved = window.localStorage.getItem("rfp-sidebar-open");
    if (saved === "1") setSidebarOpen(true);
  }, []);
  function toggleSidebar() {
    setSidebarOpen((v) => {
      window.localStorage.setItem("rfp-sidebar-open", v ? "0" : "1");
      return !v;
    });
  }

  return (
    <div className="app-shell flex min-h-screen flex-col">
      {/* 상단 헤더 — kt 로고 + Easy제안 */}
      <header className="topbar sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-[var(--border)] bg-white/90 px-4 py-3 backdrop-blur md:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={toggleSidebar}
            className="grid h-9 w-9 place-items-center rounded-lg text-neutral-600 transition hover:bg-neutral-100"
            title={sidebarOpen ? "프로젝트 목록 닫기" : "프로젝트 목록 열기"}
            aria-label="프로젝트 목록 토글"
          >
            <HamburgerIcon open={sidebarOpen} />
          </button>
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/kt-logo.png" alt="kt" width={34} height={28} priority className="h-7 w-auto" />
            <span className="h-5 w-px bg-neutral-200" />
            <span className="text-[17px] font-bold tracking-tight text-ink-900">Easy제안</span>
          </Link>
          {activeProject && (
            <span className="hidden min-w-0 items-center gap-2 md:flex">
              <span className="h-4 w-px bg-neutral-200" />
              <span className="truncate text-sm text-neutral-500" title={activeProject.title}>
                {activeProject.title}
              </span>
            </span>
          )}
        </div>
        <span className="status-pill shrink-0">
          <span className="status-dot" />
          Live
        </span>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* 사이드바 (오버레이형 — 접힘 기본) */}
        {sidebarOpen && (
          <button
            type="button"
            aria-label="사이드바 닫기"
            onClick={toggleSidebar}
            className="fixed inset-0 z-20 bg-ink-900/10 lg:hidden"
          />
        )}
        <aside
          className={`sidebar z-20 shrink-0 overflow-hidden border-r border-[var(--border)] bg-white transition-[width] duration-200 ${
            sidebarOpen ? "w-52" : "w-0"
          } ${sidebarOpen ? "fixed inset-y-0 left-0 top-[57px] lg:static lg:top-0" : ""}`}
        >
          <nav className="h-full w-52 overflow-y-auto p-2.5">
            <p className="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
              시작
            </p>
            {PRIMARY_NAV.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`nav-link mb-3 flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm ${
                    active ? "nav-link-active" : "text-neutral-600 hover:bg-neutral-50"
                  }`}
                >
                  <span className="grid h-7 w-7 place-items-center rounded-md bg-neutral-100 text-xs font-semibold">
                    {item.icon}
                  </span>
                  {item.label}
                </Link>
              );
            })}

            <p className="mb-1.5 mt-2 px-2 text-[10px] font-semibold uppercase tracking-wider text-neutral-400">
              프로젝트
            </p>
            {projects.length === 0 ? (
              <p className="px-3 py-2 text-xs leading-relaxed text-neutral-400">
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
                    activeDocId != null && (p.docId === activeDocId || p.staleDocId === activeDocId);
                  return (
                    <li key={p.key}>
                      <ProjectNavButton item={p} active={active} />
                    </li>
                  );
                })}
              </ul>
            )}
          </nav>
        </aside>

        <main className="main-content min-w-0 flex-1 px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
