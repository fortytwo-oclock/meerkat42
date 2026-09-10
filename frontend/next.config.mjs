/** @type {import('next').NextConfig} */
const HUB = process.env.HUB_URL || "http://localhost:8080";
const browserSecurityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const nextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: browserSecurityHeaders }];
  },
  async rewrites() {
    // Proxy REST + WS to the hub so the browser talks same-origin in dev.
    return [{ source: "/api/:path*", destination: `${HUB}/api/:path*` }];
  },
};

export default nextConfig;
