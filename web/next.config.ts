import type { NextConfig } from "next";

// The browser uses relative /api paths; Next proxies them to the backend.
// (WebSockets go to the backend directly — see src/lib/ws.ts.)
const BACKEND = process.env.PANTHEON_BACKEND_URL ?? "http://localhost:8710";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND}/api/:path*` }];
  },
};

export default nextConfig;
