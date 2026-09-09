/** @type {import('next').NextConfig} */
const HUB = process.env.HUB_URL || "http://localhost:8080";

const nextConfig = {
  async rewrites() {
    // Proxy REST + WS to the hub so the browser talks same-origin in dev.
    return [{ source: "/api/:path*", destination: `${HUB}/api/:path*` }];
  },
};

export default nextConfig;
