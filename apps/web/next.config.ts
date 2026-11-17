import type { NextConfig } from "next";

const API = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  reactStrictMode: true,
  // Mol* ships large ESM modules and its own workers; transpiling keeps Next
  // from choking on them and keeps the viewer chunk lazy.
  // Mol* registers query symbols and representation providers as module side
  // effects. Next's optimizePackageImports rewrites those imports and drops the
  // registrations, which surfaces as "Symbol 'structure-query.modifier.union'
  // is not implemented" at runtime. So it is deliberately not enabled here.
  transpilePackages: ["molstar"],
  async rewrites() {
    // The API is proxied through the same origin on purpose. Session cookies
    // are httpOnly and SameSite=Lax, so a cross-origin call from the browser
    // would not send them; proxying keeps everything first-party.
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
  async redirects() {
    // /projects was the old wizard, removed when the product became one
    // surface. Anyone with it open in a tab or saved as a bookmark should
    // land on the research they came for, not on a 404.
    return [
      { source: "/projects", destination: "/lab", permanent: false },
      { source: "/projects/:path*", destination: "/lab", permanent: false },
      { source: "/sessions", destination: "/lab", permanent: false },
      { source: "/sessions/:path*", destination: "/lab", permanent: false },
    ];
  },
};

export default config;
