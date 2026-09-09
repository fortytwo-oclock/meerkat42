"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { Sparkline } from "@/components/sparkline";
import { timeAgo } from "@/lib/utils";
import { api, type NodeSummary } from "@/lib/api";
import { useLiveStream } from "@/hooks/useLiveStream";

const MAX_SPARK = 30;

export function FleetMatrix({ group }: { group?: string }) {
  const [nodes, setNodes] = useState<NodeSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const cpuHistory = useRef<Record<string, number[]>>({});

  const refresh = useCallback(async () => {
    const { nodes } = await api.nodes(group);
    setNodes(nodes);
    setLoading(false);
  }, [group]);

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
        {connected ? "Live" : "Reconnecting…"} · {nodes.length} nodes
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading fleet…</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {nodes.map((n) => (
            <Link key={n.node_id} href={`/nodes/${encodeURIComponent(n.node_id)}`}>
              <Card className="transition-colors hover:border-primary/40">
                <CardContent className="space-y-3 p-3">
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="font-medium">{n.hostname}</div>
                      <div className="text-xs text-muted-foreground">{n.client_group}</div>
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
                    <Badge variant="secondary">
                      {n.containers_running}/{n.containers_total} ctr
                    </Badge>
                    {n.crash_loop_count > 0 && (
                      <Badge variant="destructive">{n.crash_loop_count} crash-loop</Badge>
                    )}
                    {n.container_updates_available > 0 && (
                      <Badge variant="warning">
                        {n.container_updates_available} image update
                      </Badge>
                    )}
                    <Badge variant={n.zfs_degraded_count > 0 ? "destructive" : "secondary"}>
                      zfs {n.zfs_pools_count}
                      {n.zfs_degraded_count > 0 ? ` · ${n.zfs_degraded_count} degraded` : ""}
                    </Badge>
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
