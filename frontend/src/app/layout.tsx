import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Meerkat42 Infrastructure Dashboard",
  description: "Distributed self-hosted infrastructure control plane",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
