import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Biostruct — therapeutic protein design",
  description:
    "Design candidate proteins that stick to the enzymes making an infection " +
    "drug-resistant. A starting point for lab work, not evidence that anything cures.",
};

export const viewport: Viewport = {
  themeColor: "#ffffff",
  colorScheme: "light",
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
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@500;600&family=IBM+Plex+Mono:wght@400;500&display=swap"
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
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:border focus:border-signal focus:bg-ground focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
