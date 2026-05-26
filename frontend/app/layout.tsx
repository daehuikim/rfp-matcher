import "./globals.css";
import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "RFP Matcher — 조견표 자동 추출 & AI 추천",
  description: "RFP/RFI 비정형 문서에서 조견표를 한 줄씩 자동 추출하고 KT AI 솔루션과 매칭합니다.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-screen antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
