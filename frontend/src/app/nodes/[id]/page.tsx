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
import { api, type MetricSeries } from "@/lib/api";

export default function NodeDetailPage({ params }: { params: { id: string } }) {
  const id = decodeURIComponent(params.id);
  const [node, setNode] = useState<any>(null);
  const [cpu, setCpu] = useState<MetricSeries | null>(null);
  const [ram, setRam] = useState<MetricSeries | null>(null);
  const [range, setRange] = useState(6);

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
                  <Badge variant={c.is_crash_loop ? "destructive" : "secondary"}>
                    {c.state}
                  </Badge>
                </div>
                <div className="text-xs text-muted-foreground">
                  {c.image} · {c.status} · restarts {c.restart_count ?? 0}
                </div>
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
