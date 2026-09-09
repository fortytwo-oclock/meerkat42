"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { api, type BackupMeta, type MetricSeries } from "@/lib/api";
import { Button } from "@/components/ui/button";

export default function NodeDetailPage({ params }: { params: { id: string } }) {
  const id = decodeURIComponent(params.id);
  const [node, setNode] = useState<any>(null);
  const [cpu, setCpu] = useState<MetricSeries | null>(null);
  const [ram, setRam] = useState<MetricSeries | null>(null);
  const [range, setRange] = useState(6);
  const [backups, setBackups] = useState<BackupMeta[] | null>(null);
  const [vaultMsg, setVaultMsg] = useState<string | null>(null);

  const loadBackups = async () => {
    try {
      setBackups((await api.backups(id)).backups);
    } catch (e: any) {
      // 402 = not licensed, 401 = needs token — hide the section quietly
      setBackups(null);
    }
  };
  useEffect(() => {
    loadBackups();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    const load = async () => {
      const [n, c, r] = await Promise.all([
        api.node(id),
        api.nodeMetrics(id, "cpu_usage_pct", range),
        api.nodeMetrics(id, "ram_usage_pct", range),
      ]);
      setNode(n);
      setCpu(c);
      setRam(r);
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [id, range]);

  if (!node) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;

  const t = node.latest_telemetry ?? {};
  const chartData = (cpu?.points ?? []).map((p, i) => ({
    ts: new Date(p.ts).toLocaleTimeString(),
    cpu: p.value,
    ram: ram?.points[i]?.value ?? null,
  }));

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <Link href="/" className="text-sm text-muted-foreground hover:underline">
        ← Fleet
      </Link>

      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{node.hostname}</h1>
          <p className="text-sm text-muted-foreground">
            {node.client_group} · {node.os ?? "?"} · {node.kernel ?? "?"} · agent{" "}
            {node.agent_version ?? "?"}
          </p>
        </div>
        <StatusBadge status={node.status} />
      </header>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>CPU / RAM</CardTitle>
          <div className="flex gap-1">
            {[6, 24, 168, 720].map((h) => (
              <button
                key={h}
                onClick={() => setRange(h)}
                className={`rounded border px-2 py-0.5 text-xs ${
                  range === h ? "bg-primary text-primary-foreground" : ""
                }`}
              >
                {h < 24 ? `${h}h` : `${h / 24}d`}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <XAxis dataKey="ts" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 10 }} domain={[0, 100]} />
                <Tooltip />
                <Area dataKey="cpu" stroke="#6366f1" fill="#6366f133" />
                <Area dataKey="ram" stroke="#10b981" fill="#10b98133" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            resolution: {cpu?.resolution ?? "-"}
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Containers ({(t.containers ?? []).length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {(t.containers ?? []).map((c: any) => (
              <div key={c.id ?? c.name} className="rounded-md border p-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{c.name}</span>
                  <div className="flex items-center gap-1">
                    {c.update_available && <Badge variant="warning">update available</Badge>}
                    <Badge variant={c.is_crash_loop ? "destructive" : "secondary"}>
                      {c.state}
                    </Badge>
                  </div>
                </div>
                <div className="text-xs text-muted-foreground">
                  {c.image} · {c.status} · restarts {c.restart_count ?? 0}
                </div>
                {c.update_check_status && c.update_check_status !== "DISABLED" && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    image check: {String(c.update_check_status).toLowerCase().replaceAll("_", " ")}
                    {c.update_checked_at
                      ? ` · ${new Date(c.update_checked_at).toLocaleString()}`
                      : ""}
                  </div>
                )}
              </div>
            ))}
            {(t.containers ?? []).length === 0 && (
              <p className="text-sm text-muted-foreground">None reported.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>ZFS pools ({(t.zfs ?? []).length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {(t.zfs ?? []).map((p: any) => (
              <div key={p.pool_name} className="rounded-md border p-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{p.pool_name}</span>
                  <Badge variant={p.state === "ONLINE" ? "success" : "destructive"}>
                    {p.state}
                  </Badge>
                </div>
                <div className="text-xs text-muted-foreground">
                  {p.capacity_pct ?? "?"}% used · errors r{p.read_errors ?? 0}/w
                  {p.write_errors ?? 0}/c{p.cksum_errors ?? 0}
                </div>
              </div>
            ))}
            {(t.zfs ?? []).length === 0 && (
              <p className="text-sm text-muted-foreground">None reported.</p>
            )}
          </CardContent>
        </Card>

        {backups !== null && (
          <Card className="md:col-span-2">
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>Infrastructure Vault</CardTitle>
              <Button
                size="sm"
                variant="outline"
                onClick={async () => {
                  setVaultMsg(null);
                  try {
                    await api.requestBackup(id);
                    setVaultMsg("Backup requested — the agent is encrypting now.");
                    setTimeout(loadBackups, 2000);
                  } catch (e: any) {
                    setVaultMsg(e.message);
                  }
                }}
              >
                Request backup
              </Button>
            </CardHeader>
            <CardContent className="space-y-2">
              {vaultMsg && <p className="text-xs text-muted-foreground">{vaultMsg}</p>}
              {backups.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  No backups yet. Encrypted compose / .env snapshots stored on the hub.
                </p>
              )}
              {backups.map((b) => (
                <div key={b.id} className="flex items-center justify-between rounded-md border p-2 text-sm">
                  <span>
                    {new Date(b.requested_at).toLocaleString()} · {b.requested_by}
                  </span>
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">
                    {b.status === "COMPLETE"
                      ? `${b.file_count} file(s), ${b.total_bytes} B · key ${b.key_fingerprint}`
                      : b.status === "ERROR"
                        ? b.error
                        : "pending…"}
                    <Badge
                      variant={
                        b.status === "COMPLETE"
                          ? "success"
                          : b.status === "ERROR"
                            ? "destructive"
                            : "secondary"
                      }
                    >
                      {b.status}
                    </Badge>
                  </span>
                </div>
              ))}
              <p className="text-xs text-muted-foreground">
                Ciphertext only — decrypt locally with{" "}
                <code>scripts/vault_decrypt.py</code>.
              </p>
            </CardContent>
          </Card>
        )}

        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>Disks ({(t.disks ?? []).length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {(t.disks ?? []).map((d: any) => (
              <div
                key={d.device_name}
                className="flex items-center justify-between rounded-md border p-2 text-sm"
              >
                <span>
                  {d.device_name} · {d.model} · SN {d.serial}
                </span>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  {d.temperature_c}°C · {d.power_on_hours}h
                  <Badge variant={d.health === "PASSED" ? "success" : "destructive"}>
                    {d.health}
                  </Badge>
                </span>
              </div>
            ))}
            {(t.disks ?? []).length === 0 && (
              <p className="text-sm text-muted-foreground">None reported.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
