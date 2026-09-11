// Typed client for the Control Plane Hub REST + WebSocket API.

export interface NodeSummary {
  node_id: string;
  hostname: string;
  arch: string | null;
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
  container_updates_available: number;
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

export interface Branding {
  white_label: boolean;
  product_name: string;
  logo_url: string | null;
  primary_color: string | null;
  customer_name: string | null;
  tier: string | null;
}

export interface ApiTokenInfo {
  id: string;
  name: string;
  role: string;
  group_id: string | null;
  created_at: string;
  created_by: string;
  last_used_at: string | null;
  revoked: boolean;
}

export interface PrincipalInfo {
  label: string;
  role: "owner" | "admin" | "viewer";
  tenant_id: string;
  group_id: string | null;
}

export interface AgentTokenInfo {
  id: string;
  name: string;
  group_slug: string;
  created_at: string;
  created_by: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked: boolean;
}

export interface BackupMeta {
  id: string;
  node_id: string;
  status: "PENDING" | "COMPLETE" | "ERROR";
  error: string | null;
  file_count: number;
  total_bytes: number;
  key_fingerprint: string | null;
  requested_by: string;
  requested_at: string;
  completed_at: string | null;
}

export interface RemoteCommand {
  id: string;
  node_id: string;
  action: string;
  status: "AWAITING_APPROVAL" | "QUEUED" | "DISPATCHED" | "SUCCEEDED" | "FAILED" | "TIMED_OUT" | "CANCELED";
  requested_by: string;
  destructive: boolean;
  approval_required: boolean;
  attempts: number;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface MetricSeries {
  node_id: string;
  metric: string;
  resolution: string;
  points: { ts: string; value: number }[];
}

export interface BillingSubscription {
  stripe_customer_id: string;
  stripe_subscription_id: string;
  stripe_product_id: string;
  status: string;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
}

export interface BillingPlan {
  product_id: string;
  tier: string;
  max_nodes: number;
  features: string[];
}

export interface IssuedBillingLicense {
  license_token: string;
  claims: Record<string, unknown>;
  issued_at: string;
  expires_at: string | null;
  delivery_email: string | null;
  delivery_status: "PENDING" | "RETRY" | "SENT" | "FAILED" | "SKIPPED";
  delivery_attempts: number;
  delivered_at: string | null;
  delivery_error: string | null;
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
  const t = getAdminToken();
  return t ? { "X-Admin-Token": t } : {};
}

export function getAdminToken(): string {
  try {
    return localStorage.getItem("hub_admin_token") ?? "";
  } catch {
    return "";
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
  currentPrincipal: () => j<PrincipalInfo>(`/api/v1/auth/me`),
  nodes: (group?: string) =>
    j<{ nodes: NodeSummary[]; total: number }>(
      `/api/v1/nodes${group ? `?client_group=${encodeURIComponent(group)}` : ""}`,
    ),
  node: (id: string) => j<any>(`/api/v1/nodes/${encodeURIComponent(id)}`),
  deleteNode: (id: string) =>
    j<{ deleted: string }>(`/api/v1/nodes/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
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

  branding: () => j<Branding>(`/api/v1/branding`),

  tokens: () => j<{ tokens: ApiTokenInfo[] }>(`/api/v1/tokens`),
  createToken: (body: Record<string, unknown>) =>
    j<ApiTokenInfo & { token: string }>(`/api/v1/tokens`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  revokeToken: (id: string) => j(`/api/v1/tokens/${id}`, { method: "DELETE" }),

  agentTokens: () => j<{ tokens: AgentTokenInfo[] }>(`/api/v1/agent-tokens`),
  createAgentToken: (body: Record<string, unknown>) =>
    j<AgentTokenInfo & { token: string }>(`/api/v1/agent-tokens`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  revokeAgentToken: (id: string) =>
    j(`/api/v1/agent-tokens/${id}`, { method: "DELETE" }),

  backups: (nodeId: string) =>
    j<{ backups: BackupMeta[] }>(`/api/v1/nodes/${encodeURIComponent(nodeId)}/backups`),
  requestBackup: (nodeId: string) =>
    j<{ backup_id: string; status: string }>(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/backup`,
      { method: "POST" },
    ),

  commands: (nodeId: string) =>
    j<{ commands: RemoteCommand[] }>(
      `/api/v1/nodes/${encodeURIComponent(nodeId)}/commands`,
    ),
  createCommand: (nodeId: string, action: string, payload: Record<string, unknown> = {}) =>
    j<RemoteCommand>(`/api/v1/nodes/${encodeURIComponent(nodeId)}/commands`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ action, payload }),
    }),
  cancelCommand: (id: string) =>
    j<RemoteCommand>(`/api/v1/commands/${id}/cancel`, { method: "POST" }),
  approveCommand: (id: string) =>
    j<RemoteCommand>(`/api/v1/commands/${id}/approve`, { method: "POST" }),
  emergencyStatus: () => j<{ enabled: boolean }>(`/api/v1/emergency/status`),
  createEmergencySession: (totp: string) =>
    j<{ grant: string; expires_at: string }>(`/api/v1/emergency/sessions`, {
      method: "POST",
      headers: { "X-TOTP-Code": totp },
    }),
  runDiagnostic: (nodeId: string, diagnostic: string, grant: string) =>
    j<RemoteCommand>(`/api/v1/nodes/${encodeURIComponent(nodeId)}/diagnostics`, {
      method: "POST",
      headers: { "X-Emergency-Grant": grant },
      body: JSON.stringify({ diagnostic }),
    }),

  billingSubscription: () =>
    j<BillingSubscription>(`/api/v1/billing/subscription`),
  billingLicense: () => j<IssuedBillingLicense>(`/api/v1/billing/license`),
  retryBillingLicenseDelivery: () =>
    j<{ status: string }>(`/api/v1/billing/license/retry`, { method: "POST" }),
  billingPlans: () => j<{ plans: BillingPlan[] }>(`/api/v1/billing/plans`),
  createCheckout: (product_id: string, customer_email?: string) =>
    j<{ url: string }>(`/api/v1/billing/checkout`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ product_id, customer_email: customer_email || null }),
    }),
  createBillingPortal: () =>
    j<{ url: string }>(`/api/v1/billing/portal`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),
};

export function wsUrl(path: string): string {
  if (typeof window === "undefined") return "";
  const base = process.env.NEXT_PUBLIC_WS_BASE;
  if (base) return `${base}${path}`;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}
