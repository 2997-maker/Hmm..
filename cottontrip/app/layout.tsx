import type { Metadata } from "next";
import "./globals.css";
import PackingApp from "./packing-app";
import "./trip.css";

export const metadata: Metadata = {
  title: "cottontrip · 여행 짐 확인",
  description: "챙긴 짐을 하나씩 확인하는 여행 준비 시제품",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="antialiased"><PackingApp/>{children}</body>
    </html>
  );
}
