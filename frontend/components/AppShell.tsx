"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "업로드", icon: "↑" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="app-shell flex min-h-screen">
      <aside className="sidebar hidden w-56 shrink-0 flex-col border-r border-slate-200/80 bg-white md:flex">
        <div className="flex items-center gap-2.5 border-b border-slate-100 px-5 py-5">
          <span className="brand-mark grid h-9 w-9 place-items-center rounded-lg text-sm font-bold text-white">
            R
          </span>
          <div>
            <div className="text-sm font-semibold text-slate-900">RFP Matcher</div>
            <div className="text-[10px] text-slate-500">조견표 · AI 추천</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 p-3">
          {NAV.map((item) => {
            const active = pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`nav-link flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm ${
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
        </nav>
        <div className="border-t border-slate-100 p-4 text-[10px] leading-relaxed text-slate-400">
          Open Template Hub 스타일 레이아웃
          <br />
          KT K intelligence Suite 매칭
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
          <div className="hidden text-xs text-slate-500 md:block">
            RFP/RFI 조견표 자동 추출 · O/△/X AI 리스크
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
