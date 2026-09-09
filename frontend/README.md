# Control Plane Dashboard

Next.js 14 (App Router) + Tailwind + shadcn-style UI. Connects to the Hub's REST
API and the `/api/v1/ui/stream` WebSocket for live updates.

## Develop

```bash
cp .env.example .env      # set HUB_URL if the hub isn't on :8080
npm install
npm run dev               # http://localhost:3000
```

`next.config.mjs` proxies `/api/*` to `HUB_URL`, so the browser stays same-origin
(REST + WebSocket both work through the proxy in dev).

## Views

- **`/`** — Fleet Matrix: node grid with live status, CPU sparkline, container /
  ZFS badges; client-group filter chips; Active Alerts panel with ack; License
  modal (paste signed Ed25519 JWT, node-usage bar, expiry).
- **`/nodes/[id]`** — Node detail: CPU/RAM history chart (6h/24h/7d/30d, backed by
  the hub's rollups), Docker container cards, ZFS pool cards, SMART disk table.
- **`/settings`** — alert rules, notification channels, API/client-portal tokens,
  and owner-only Stripe Checkout/Billing Portal controls, subscription status,
  and issued offline-license retrieval.

## Structure

```
src/
  app/                  routes (page.tsx = dashboard, nodes/[id] = detail)
  components/
    ui/                 card, badge, button, dialog, input primitives
    fleet-matrix.tsx    live node grid
    alerts-panel.tsx    active alerts + ack
    license-modal.tsx   license management
    sparkline.tsx       dependency-free svg sparkline
  hooks/useLiveStream.ts  reconnecting WS subscription
  lib/api.ts            typed hub client + wsUrl()
```
