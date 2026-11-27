"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Battery, Cpu, Laptop, MapPin, Server, Smartphone } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { Sparkline } from "@/components/sparkline";
import { timeAgo } from "@/lib/utils";
import { api, type DeviceClass, type NodeSummary } from "@/lib/api";
import { useLiveStream } from "@/hooks/useLiveStream";

const MAX_SPARK = 30;

function DeviceIcon({ deviceClass }: { deviceClass?: DeviceClass }) {
  switch (deviceClass) {
    case "workstation":
      return <Laptop className="h-3.5 w-3.5 text-muted-foreground" />;
    case "mobile":
      return <Smartphone className="h-3.5 w-3.5 text-muted-foreground" />;
    case "iot":
      return <Cpu className="h-3.5 w-3.5 text-muted-foreground" />;
    default:
      return <Server className="h-3.5 w-3.5 text-muted-foreground" />;
  }
}

export function FleetMatrix({
  group,
  deviceClass,
}: {
  group?: string;
  deviceClass?: DeviceClass;
}) {
  const [nodes, setNodes] = useState<NodeSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cpuHistory = useRef<Record<string, number[]>>({});

  const refresh = useCallback(async () => {
    try {
      const { nodes } = await api.nodes(group, deviceClass);
      setNodes(nodes);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load the fleet");
    } finally {
      setLoading(false);
    }
  }, [group, deviceClass]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  const { connected } = useLiveStream((e) => {
    if (e.type === "telemetry" && e.node_id && e.scalars) {
      const hist = cpuHistory.current[e.node_id] ?? [];
      hist.push(e.scalars.cpu_usage_pct ?? 0);
      cpuHistory.current[e.node_id] = hist.slice(-MAX_SPARK);
      setNodes((prev) =>
        prev.map((n) =>
          n.node_id === e.node_id
            ? {
                ...n,
                status: "ONLINE",
                last_seen: e.ts ?? n.last_seen,
                cpu_usage_pct: e.scalars!.cpu_usage_pct ?? n.cpu_usage_pct,
                ram_usage_pct: e.scalars!.ram_usage_pct ?? n.ram_usage_pct,
                load_1: e.scalars!.load_1 ?? n.load_1,
                crash_loop_count: e.scalars!.crash_loop_count ?? n.crash_loop_count,
                zfs_degraded_count: e.scalars!.zfs_degraded_count ?? n.zfs_degraded_count,
                battery_pct:
                  e.scalars!["battery.pct"] !== undefined
                    ? e.scalars!["battery.pct"]
                    : n.battery_pct,
              }
            : n,
        ),
      );
    } else if (e.type === "node.connected") {
      refresh();
    }
  });

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span
          className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-500" : "bg-amber-500"}`}
        />
        {connected ? "Live" : "Reconnecting…"} · {nodes.length} devices
      </div>

      {error ? (
        <div className="rounded-lg border p-4 text-sm">
          <p className="font-medium">Operator token required</p>
          <p className="mt-1 text-muted-foreground">
            Open Settings and save an owner, admin, or viewer token for this browser.
          </p>
          <Link href="/settings" className="mt-3 inline-block underline underline-offset-4">
            Open Settings
          </Link>
        </div>
      ) : loading ? (
        <p className="text-sm text-muted-foreground">Loading fleet…</p>
      ) : nodes.length === 0 ? (
        <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
          No devices match this view yet. Click <strong>Add device</strong> above to enroll a server, workstation, or phone.
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {nodes.map((n) => (
            <Link key={n.node_id} href={`/nodes/${encodeURIComponent(n.node_id)}`}>
              <Card className="transition-colors hover:border-primary/40">
                <CardContent className="space-y-3 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <DeviceIcon deviceClass={n.device_class} />
                        <span className="truncate font-medium">{n.hostname}</span>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {n.client_group}
                        {n.platform ? ` · ${n.platform}` : ""}
                      </div>
                    </div>
                    <StatusBadge status={n.status} />
                  </div>

                  <div className="flex items-center justify-between text-xs">
                    <div className="space-y-0.5">
                      <div>CPU {n.cpu_usage_pct.toFixed(0)}%</div>
                      <div>RAM {n.ram_usage_pct.toFixed(0)}%</div>
                      <div>load {n.load_1.toFixed(2)}</div>
                    </div>
                    <Sparkline
                      values={cpuHistory.current[n.node_id] ?? [n.cpu_usage_pct]}
                      color="hsl(var(--primary))"
                    />
                  </div>

                  <div className="flex flex-wrap gap-1">
                    {n.battery_pct !== undefined && n.battery_pct !== null && (
                      <Badge variant="secondary" className="gap-1">
                        <Battery className="h-3 w-3" />
                        {n.battery_pct.toFixed(0)}%
                      </Badge>
                    )}
                    {n.last_location && (
                      <Badge variant="secondary" className="gap-1">
                        <MapPin className="h-3 w-3" />
                        {n.last_location.zone ||
                          `${n.last_location.lat.toFixed(2)}, ${n.last_location.lon.toFixed(2)}`}
                      </Badge>
                    )}
                    {n.containers_total > 0 && (
                      <Badge variant="secondary">
                        {n.containers_running}/{n.containers_total} ctr
                      </Badge>
                    )}
                    {n.crash_loop_count > 0 && (
                      <Badge variant="destructive">{n.crash_loop_count} crash-loop</Badge>
                    )}
                    {n.container_updates_available > 0 && (
                      <Badge variant="warning">
                        {n.container_updates_available} image update
                      </Badge>
                    )}
                    {n.zfs_pools_count > 0 && (
                      <Badge variant={n.zfs_degraded_count > 0 ? "destructive" : "secondary"}>
                        zfs {n.zfs_pools_count}
                        {n.zfs_degraded_count > 0 ? ` · ${n.zfs_degraded_count} degraded` : ""}
                      </Badge>
                    )}
                  </div>

                  <div className="text-xs text-muted-foreground">
                    seen {timeAgo(n.last_seen)}
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
