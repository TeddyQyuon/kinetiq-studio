import type { Metadata } from "next";
import "./globals.css";
import { StudioShell } from "@/components/StudioShell";

export const metadata: Metadata = {
  title: "Kinetiq Studio — AI Faceless Video Platform",
  description: "AI-directed 1080p/4K educational video studio backed by Google Colab & Google Drive",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-[#09090b] text-zinc-100 antialiased">
        <StudioShell>{children}</StudioShell>
      </body>
    </html>
  );
}
