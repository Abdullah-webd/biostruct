import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Biostruct — therapeutic protein design",
  description:
    "Design candidate protein binders against drug-resistant bacterial targets. " +
    "Computational prioritisation, not evidence of therapeutic effect.",
};

export const viewport: Viewport = {
  themeColor: "#0a0e14",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning is required here, not cosmetic. The inline
    // script below adds a class to <html> before React hydrates, so the server
    // markup and the client tree differ on this element. Without this, React
    // reports "this won't be patched up" and abandons hydration, which leaves
    // the whole app non-interactive and makes forms fall back to native
    // submits.
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=Archivo+Expanded:wght@600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
        {/* Marks JS as present so the reveal gate can hide pending elements.
            Without JS nothing is hidden, so the page is readable regardless. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `document.documentElement.classList.add('js-ready')`,
          }}
        />
      </head>
      <body className="min-h-screen antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:border focus:border-signal focus:bg-panel focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
