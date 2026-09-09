"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  setAdminToken,
  api,
  type AlertRule,
  type ApiTokenInfo,
  type Channel,
} from "@/lib/api";

const KIND_HINT: Record<string, string> = {
  discord: '{ "url": "https://discord.com/api/webhooks/..." }',
  slack: '{ "url": "https://hooks.slack.com/services/..." }',
  webhook: '{ "url": "https://example.com/hook" }',
  smtp: '{ "host": "smtp.example.com", "port": 587, "username": "u", "password": "p", "from": "alerts@x.com", "to": ["ops@x.com"] }',
};

export default function SettingsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [tokens, setTokens] = useState<ApiTokenInfo[] | null>(null);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [token, setToken] = useState("");

  // new channel form
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Channel["kind"]>("discord");
  const [config, setConfig] = useState("");
  const [minSev, setMinSev] = useState("WARNING");

  const load = useCallback(async () => {
    try {
      const [c, r] = await Promise.all([api.channels(), api.alertRules()]);
      setChannels(c.channels);
      setRules(r.rules);
      setErr(null);
    } catch (e: any) {
      setErr(e.message);
    }
    try {
      setTokens((await api.tokens()).tokens);
    } catch {
      setTokens(null); // not owner / not configured
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const guard = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await load();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <Link href="/" className="text-sm text-muted-foreground hover:underline">
        ← Fleet
      </Link>
      <h1 className="text-xl font-semibold">Settings</h1>
      {err && <p className="text-sm text-destructive">{err}</p>}

      <Card>
        <CardHeader>
          <CardTitle>Operator token</CardTitle>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Input
            type="password"
            placeholder="ADMIN_TOKEN (only if the hub sets one)"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <Button
            variant="outline"
            onClick={() => {
              setAdminToken(token.trim());
              load();
            }}
          >
            Save
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Alert triggers</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {rules.map((r) => (
            <div key={r.id} className="flex items-center justify-between rounded-md border p-2.5 text-sm">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium">{r.name}</span>
                  <Badge variant="secondary">{r.severity}</Badge>
                  {r.is_builtin && <Badge variant="outline">builtin</Badge>}
                </div>
                <p className="text-xs text-muted-foreground">
                  {r.metric} {r.comparator} {r.threshold ?? "—"}
                  {r.duration_sec ? ` for ${r.duration_sec}s` : ""}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <Switch
                  checked={r.enabled}
                  onCheckedChange={(v) =>
                    guard(() => api.updateAlertRule(r.id, { enabled: v }))
                  }
                />
                {!r.is_builtin && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => guard(() => api.deleteAlertRule(r.id))}
                  >
                    Delete
                  </Button>
                )}
              </div>
            </div>
          ))}
          <NewRuleForm onCreate={(b) => guard(() => api.createAlertRule(b))} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Notification channels</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {channels.map((c) => (
            <div key={c.id} className="rounded-md border p-2.5 text-sm">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{c.name}</span>
                  <Badge variant="secondary">{c.kind}</Badge>
                  <Badge variant="outline">≥ {c.min_severity}</Badge>
                  {c.muted_until && new Date(c.muted_until) > new Date() && (
                    <Badge variant="warning">muted</Badge>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Switch
                    checked={c.enabled}
                    onCheckedChange={(v) =>
                      guard(() => api.updateChannel(c.id, { enabled: v }))
                    }
                  />
                  <Button size="sm" variant="outline" onClick={() => guard(() => api.testChannel(c.id))}>
                    Test
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => guard(() => api.muteChannel(c.id, 60))}>
                    Mute 1h
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => guard(() => api.deleteChannel(c.id))}>
                    Delete
                  </Button>
                </div>
              </div>
              {c.last_error && (
                <p className="mt-1 text-xs text-destructive">last error: {c.last_error}</p>
              )}
            </div>
          ))}

          <div className="space-y-2 rounded-md border border-dashed p-3">
            <div className="grid gap-2 sm:grid-cols-2">
              <Input placeholder="Channel name" value={name} onChange={(e) => setName(e.target.value)} />
              <select
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                value={kind}
                onChange={(e) => setKind(e.target.value as Channel["kind"])}
              >
                {["discord", "slack", "webhook", "smtp"].map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </div>
            <Input
              placeholder={KIND_HINT[kind]}
              value={config}
              onChange={(e) => setConfig(e.target.value)}
            />
            <div className="flex items-center gap-2">
              <select
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                value={minSev}
                onChange={(e) => setMinSev(e.target.value)}
              >
                {["INFO", "WARNING", "HIGH", "CRITICAL"].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
              <Button
                size="sm"
                disabled={!name.trim() || !config.trim()}
                onClick={() =>
                  guard(async () => {
                    let parsed: Record<string, unknown>;
                    try {
                      parsed = JSON.parse(config);
                    } catch {
                      throw new Error("config must be valid JSON");
                    }
                    await api.createChannel({
                      name: name.trim(),
                      kind,
                      config: parsed,
                      min_severity: minSev,
                    });
                    setName("");
                    setConfig("");
                  })
                }
              >
                Add channel
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {tokens !== null && (
        <Card>
          <CardHeader>
            <CardTitle>API tokens & client portals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {newToken && (
              <div className="rounded-md border border-emerald-600/40 bg-emerald-600/10 p-2 text-xs">
                Copy now — shown once:
                <code className="ml-1 break-all font-mono">{newToken}</code>
              </div>
            )}
            {tokens.map((t) => (
              <div key={t.id} className="flex items-center justify-between rounded-md border p-2.5 text-sm">
                <div>
                  <span className="font-medium">{t.name}</span>{" "}
                  <Badge variant="secondary">{t.role}</Badge>{" "}
                  {t.group_id && <Badge variant="outline">scoped</Badge>}
                  {t.revoked && <Badge variant="destructive">revoked</Badge>}
                  <p className="text-xs text-muted-foreground">
                    by {t.created_by} ·{" "}
                    {t.last_used_at ? `last used ${new Date(t.last_used_at).toLocaleString()}` : "never used"}
                  </p>
                </div>
                {!t.revoked && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => guard(() => api.revokeToken(t.id))}
                  >
                    Revoke
                  </Button>
                )}
              </div>
            ))}
            <NewTokenForm
              onCreate={async (b) => {
                try {
                  const res = await api.createToken(b);
                  setNewToken(res.token);
                  await load();
                } catch (e: any) {
                  setErr(e.message);
                }
              }}
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function NewTokenForm({
  onCreate,
}: {
  onCreate: (b: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("viewer");
  const [groupSlug, setGroupSlug] = useState("");
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-dashed p-3">
      <Input
        className="w-40"
        placeholder="Token name"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <select
        className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        value={role}
        onChange={(e) => setRole(e.target.value)}
      >
        {["viewer", "admin", "owner"].map((r) => (
          <option key={r}>{r}</option>
        ))}
      </select>
      {role === "viewer" && (
        <Input
          className="w-40"
          placeholder="group slug (optional)"
          value={groupSlug}
          onChange={(e) => setGroupSlug(e.target.value)}
        />
      )}
      <Button
        size="sm"
        disabled={!name.trim()}
        onClick={() => {
          onCreate({
            name: name.trim(),
            role,
            group_slug: role === "viewer" && groupSlug.trim() ? groupSlug.trim() : null,
          });
          setName("");
          setGroupSlug("");
        }}
      >
        Create token
      </Button>
    </div>
  );
}

function NewRuleForm({ onCreate }: { onCreate: (b: Record<string, unknown>) => void }) {
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("cpu_usage_pct");
  const [comparator, setComparator] = useState("gt");
  const [threshold, setThreshold] = useState("90");
  const [severity, setSeverity] = useState("WARNING");

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-dashed p-3">
      <Input
        className="w-40"
        placeholder="Rule name"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <select
        className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        value={metric}
        onChange={(e) => setMetric(e.target.value)}
      >
        {["cpu_usage_pct", "ram_usage_pct", "load_1"].map((m) => (
          <option key={m}>{m}</option>
        ))}
      </select>
      <select
        className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        value={comparator}
        onChange={(e) => setComparator(e.target.value)}
      >
        <option value="gt">&gt;</option>
        <option value="lt">&lt;</option>
      </select>
      <Input
        className="w-20"
        type="number"
        value={threshold}
        onChange={(e) => setThreshold(e.target.value)}
      />
      <select
        className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        value={severity}
        onChange={(e) => setSeverity(e.target.value)}
      >
        {["INFO", "WARNING", "HIGH", "CRITICAL"].map((s) => (
          <option key={s}>{s}</option>
        ))}
      </select>
      <Button
        size="sm"
        disabled={!name.trim()}
        onClick={() => {
          onCreate({
            name: name.trim(),
            metric,
            comparator,
            threshold: Number(threshold),
            severity,
          });
          setName("");
        }}
      >
        Add rule
      </Button>
    </div>
  );
}
