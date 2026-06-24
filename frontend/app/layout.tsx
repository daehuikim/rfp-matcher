import "./globals.css";
import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";
import { WorkspaceProvider } from "@/context/WorkspaceProvider";
import { fetchWorkspaceBootstrap } from "@/lib/workspace-server";

export const metadata: Metadata = {
  title: "KT Easy제안 — RFP 조견표 자동화",
  description: "RFP/RFI 비정형 문서에서 조견표를 자동 추출하고 KT AI 솔루션과 매칭합니다.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const bootstrap = await fetchWorkspaceBootstrap();
  return (
    <html lang="ko">
      <body className="min-h-screen antialiased">
        <WorkspaceProvider
          initialServerSessions={bootstrap.serverSessions}
          initialCachedProjects={bootstrap.cachedProjects}
          initialBackendReachable={bootstrap.backendReachable}
        >
          <AppShell>{children}</AppShell>
        </WorkspaceProvider>
      </body>
    </html>
  );
}
