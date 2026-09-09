// Typed client for the Control Plane Hub REST + WebSocket API.

export interface NodeSummary {
  node_id: string;
  hostname: string;
  client_group: string;
  status: "ONLINE" | "STALE" | "OFFLINE" | "UNKNOWN";
  last_seen: string | null;
  agent_version: string | null;
  cpu_usage_pct: number;
  ram_usage_pct: number;
  load_1: number;
  containers_total: number;
  containers_running: number;
  crash_loop_count: number;
  zfs_pools_count: number;
  zfs_degraded_count: number;
}

export interface AlertEvent {
  id: string;
  severity: "INFO" | "WARNING" | "HIGH" | "CRITICAL";
  node_id: string;
  type: string;
  subject: string;
  message: string;
  state: "FIRING" | "ACKNOWLEDGED" | "RESOLVED";
  value: number | null;
  started_at: string;
  acknowledged_by: string | null;
}

export interface LicenseInfo {
  active_tier: string;
  customer_name: string;
  max_nodes: number;
  current_nodes_count: number;
  features: string[];
  expires_at: number;
  is_unlimited: boolean;
  is_custom_license: boolean;
  white_label_logo: string | null;
}

export interface Group {
  slug: string;
  name: string;
  description: string | null;
  node_count: number;
}

export interface Channel {
  id: string;
  name: string;
  kind: "discord" | "slack" | "smtp" | "webhook";
  config: Record<string, unknown>;
  min_severity: string;
  enabled: boolean;
  muted_until: string | null;
  last_error: string | null;
  last_sent_at: string | null;
}

export interface AlertRule {
  id: string;
  name: string;
  metric: string;
  comparator: string;
  threshold: number | null;
  duration_sec: number;
  severity: string;
  enabled: boolean;
  is_builtin: boolean;
}

export interface MetricSeries {
  node_id: string;
  metric: string;
  resolution: string;
  points: { ts: string; value: number }[];
}

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

/** Optional operator token for mutating endpoints when the hub sets ADMIN_TOKEN. */
export function setAdminToken(token: string) {
  try {
    if (token) localStorage.setItem("hub_admin_token", token);
    else localStorage.removeItem("hub_admin_token");
  } catch {
    /* ignore */
  }
}

function adminHeader(): Record<string, string> {
  try {
    const t = localStorage.getItem("hub_admin_token");
    return t ? { "X-Admin-Token": t } : {};
  } catch {
    return {};
  }
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...adminHeader(),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  nodes: (group?: string) =>
    j<{ nodes: NodeSummary[]; total: number }>(
      `/api/v1/nodes${group ? `?client_group=${encodeURIComponent(group)}` : ""}`,
    ),
  node: (id: string) => j<any>(`/api/v1/nodes/${encodeURIComponent(id)}`),
  nodeMetrics: (id: string, metric: string, rangeHours = 6) =>
    j<MetricSeries>(
      `/api/v1/nodes/${encodeURIComponent(id)}/metrics?metric=${metric}&range_hours=${rangeHours}`,
    ),
  alerts: (group?: string) =>
    j<{ alerts: AlertEvent[]; total: number }>(
      `/api/v1/alerts${group ? `?client_group=${encodeURIComponent(group)}` : ""}`,
    ),
  ackAlert: (id: string) =>
    j(`/api/v1/alerts/${id}/ack`, { method: "POST" }),
  license: () => j<LicenseInfo>(`/api/v1/license`),
  applyLicense: (license_token: string) =>
    j<{ success: boolean; claims: Record<string, unknown> }>(`/api/v1/license`, {
      method: "POST",
      body: JSON.stringify({ license_token }),
    }),
  groups: () => j<{ groups: Group[] }>(`/api/v1/groups`),

  alertRules: () => j<{ rules: AlertRule[] }>(`/api/v1/alert-rules`),
  createAlertRule: (body: Record<string, unknown>) =>
    j(`/api/v1/alert-rules`, { method: "POST", body: JSON.stringify(body) }),
  updateAlertRule: (id: string, body: Record<string, unknown>) =>
    j(`/api/v1/alert-rules/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteAlertRule: (id: string) =>
    j(`/api/v1/alert-rules/${id}`, { method: "DELETE" }),

  channels: () => j<{ channels: Channel[] }>(`/api/v1/channels`),
  createChannel: (body: Record<string, unknown>) =>
    j<Channel>(`/api/v1/channels`, { method: "POST", body: JSON.stringify(body) }),
  updateChannel: (id: string, body: Record<string, unknown>) =>
    j<Channel>(`/api/v1/channels/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteChannel: (id: string) =>
    j(`/api/v1/channels/${id}`, { method: "DELETE" }),
  muteChannel: (id: string, minutes: number) =>
    j(`/api/v1/channels/${id}/mute`, { method: "POST", body: JSON.stringify({ minutes }) }),
  testChannel: (id: string) =>
    j<{ ok: boolean }>(`/api/v1/channels/${id}/test`, { method: "POST" }),
};

export function wsUrl(path: string): string {
  if (typeof window === "undefined") return "";
  const base = process.env.NEXT_PUBLIC_WS_BASE;
  if (base) return `${base}${path}`;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}
