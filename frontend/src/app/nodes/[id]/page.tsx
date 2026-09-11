"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { api, type BackupMeta, type MetricSeries, type RemoteCommand } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const DIAGNOSTICS = [
  ["uptime", "Uptime"],
  ["disk_usage", "Disk usage"],
  ["memory", "Memory"],
  ["network", "Network"],
  ["failed_services", "Failed services"],
  ["recent_errors", "Recent errors"],
] as const;

export default function NodeDetailPage() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);
  const [node, setNode] = useState<any>(null);
  const [cpu, setCpu] = useState<MetricSeries | null>(null);
  const [ram, setRam] = useState<MetricSeries | null>(null);
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [chartWidth, setChartWidth] = useState(0);
  const chartContainer = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState(6);
  const [backups, setBackups] = useState<BackupMeta[] | null>(null);
  const [vaultMsg, setVaultMsg] = useState<string | null>(null);
  const [commands, setCommands] = useState<RemoteCommand[] | null>(null);
  const [commandMsg, setCommandMsg] = useState<string | null>(null);
  const [pendingContainerAction, setPendingContainerAction] = useState<{
    container: string;
    action: "restart" | "stop";
  } | null>(null);
  const [containerActionBusy, setContainerActionBusy] = useState<string | null>(null);
  const [emergencyEnabled, setEmergencyEnabled] = useState(false);
  const [totp, setTotp] = useState("");
  const [emergencyGrant, setEmergencyGrant] = useState<string | null>(null);

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
    api.emergencyStatus()
      .then((status) => setEmergencyEnabled(status.enabled))
      .catch(() => setEmergencyEnabled(false));
  }, [id]);

  const loadCommands = async () => {
    try {
      setCommands((await api.commands(id)).commands);
    } catch {
      setCommands(null);
    }
  };
  useEffect(() => {
    loadCommands();
    const timer = setInterval(loadCommands, 5000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    const load = async () => {
      try {
        const [n, c, r] = await Promise.all([
          api.node(id),
          api.nodeMetrics(id, "cpu_usage_pct", range),
          api.nodeMetrics(id, "ram_usage_pct", range),
        ]);
        setNode(n);
        setCpu(c);
        setRam(r);
        setMetricsError(null);
      } catch (error) {
        setMetricsError(error instanceof Error ? error.message : "Unable to load metrics");
      }
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [id, range]);

  useEffect(() => {
    const element = chartContainer.current;
    if (!element) return;

    const measure = () => setChartWidth(Math.floor(element.getBoundingClientRect().width));
    measure();
    window.addEventListener("resize", measure);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [node?.node_id]);

  if (!node) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;

  const t = node.latest_telemetry ?? {};
  const runProxmoxAction = async (action: string, guest: any) => {
    setCommandMsg(null);
    try {
      const created = await api.createCommand(id, action, {
        node: guest.node,
        guest_type: guest.type,
        vmid: guest.vmid,
      });
      setCommandMsg(
        created.status === "AWAITING_APPROVAL"
          ? `${action.replace("proxmox_", "")} awaits a second owner's approval.`
          : `${action.replace("proxmox_", "")} queued for ${guest.name || guest.vmid}.`,
      );
      await loadCommands();
    } catch (e: any) {
      setCommandMsg(e.message);
    }
  };
  const queueContainerAction = async (
    action: "docker_pull_image" | "docker_restart_container" | "docker_stop_container",
    container: any,
  ) => {
    const busyKey = `${container.name}:${action}`;
    setCommandMsg(null);
    setContainerActionBusy(busyKey);
    try {
      const payload = action === "docker_pull_image"
        ? { image: container.image }
        : { container: container.name };
      const created = await api.createCommand(id, action, payload);
      const verb = action === "docker_pull_image"
        ? "Image pull"
        : action === "docker_restart_container"
          ? "Restart"
          : "Stop";
      setCommandMsg(
        created.status === "AWAITING_APPROVAL"
          ? `${verb} for ${container.name} awaits a second owner's approval.`
          : `${verb} queued for ${container.name}.`,
      );
      setPendingContainerAction(null);
      await loadCommands();
    } catch (error) {
      setCommandMsg(error instanceof Error ? error.message : "Container action failed");
    } finally {
      setContainerActionBusy(null);
    }
  };
  const chartData = (cpu?.points ?? []).map((p, i) => ({
    ts: new Date(p.ts).toLocaleTimeString(),
    cpu: p.value,
    ram: ram?.points[i]?.value ?? null,
  }));

  return (
    <main className="mx-auto max-w-6xl space-y-6 p-4 sm:p-6">
      <Link href="/" className="text-sm text-muted-foreground hover:underline">
        ← Fleet
      </Link>

      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold">{node.hostname}</h1>
          <p className="break-words text-sm text-muted-foreground">
            {node.client_group} · {node.os ?? "?"} {node.arch ?? "?"} · {node.kernel ?? "?"} · agent{" "}
            {node.agent_version ?? "?"}
          </p>
        </div>
        <StatusBadge status={node.status} />
      </header>

      <Card className="min-w-0 overflow-hidden">
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
          <div ref={chartContainer} className="h-64 w-full overflow-hidden">
            {metricsError ? (
              <div className="flex h-full items-center justify-center text-sm text-destructive" role="alert">
                Graph unavailable: {metricsError}
              </div>
            ) : chartData.length === 0 ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                Waiting for metric samples…
              </div>
            ) : chartWidth > 0 ? (
              <AreaChart width={chartWidth} height={256} data={chartData}>
                <XAxis dataKey="ts" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 10 }} domain={[0, 100]} />
                <Tooltip />
                <Area dataKey="cpu" name="CPU" stroke="#6366f1" fill="#6366f133" isAnimationActive={false} />
                <Area dataKey="ram" name="RAM" stroke="#10b981" fill="#10b98133" isAnimationActive={false} />
              </AreaChart>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            resolution: {cpu?.resolution ?? "-"}
          </p>
        </CardContent>
      </Card>

      <div className="grid min-w-0 gap-4 md:grid-cols-2">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>Containers ({(t.containers ?? []).length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {(t.containers ?? []).map((c: any) => (
              <div key={c.id ?? c.name} className="min-w-0 rounded-md border p-2 text-sm">
                <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                  <span className="min-w-0 break-all font-medium">{c.name}</span>
                  <div className="flex flex-wrap items-center gap-1">
                    {c.update_available && <Badge variant="warning">update available</Badge>}
                    <Badge variant={c.is_crash_loop ? "destructive" : "secondary"}>
                      {c.state}
                    </Badge>
                  </div>
                </div>
                <div className="break-all text-xs text-muted-foreground">
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
                {commands !== null && (
                  <div className="mt-3 space-y-2">
                    <div className="flex flex-wrap gap-2" role="group" aria-label={`Actions for ${c.name}`}>
                      {c.state === "running" && !String(c.image).startsWith("sha256:") && (
                        <Button
                          className="min-h-11"
                          size="sm"
                          variant="outline"
                          disabled={containerActionBusy !== null}
                          onClick={() => queueContainerAction("docker_pull_image", c)}
                        >
                          {containerActionBusy === `${c.name}:docker_pull_image` ? "Queuing…" : "Pull update"}
                        </Button>
                      )}
                      <Button
                        className="min-h-11"
                        size="sm"
                        variant="outline"
                        disabled={containerActionBusy !== null}
                        onClick={() => setPendingContainerAction({ container: c.name, action: "restart" })}
                      >
                        Restart
                      </Button>
                      {c.state === "running" && (
                        <Button
                          className="min-h-11"
                          size="sm"
                          variant="destructive"
                          disabled={containerActionBusy !== null}
                          onClick={() => setPendingContainerAction({ container: c.name, action: "stop" })}
                        >
                          Stop
                        </Button>
                      )}
                    </div>
                    {pendingContainerAction?.container === c.name && (
                      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3" role="alert">
                        <p className="text-xs">
                          {pendingContainerAction?.action === "stop"
                            ? `Stop ${c.name}? Its service will remain unavailable until it is started again.`
                            : `Restart ${c.name}? Its service may be briefly unavailable.`}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <Button
                            className="min-h-11"
                            size="sm"
                            variant="destructive"
                            disabled={containerActionBusy !== null}
                            onClick={() => {
                              const action = pendingContainerAction?.action;
                              if (!action) return;
                              queueContainerAction(
                                action === "stop"
                                  ? "docker_stop_container"
                                  : "docker_restart_container",
                                c,
                              );
                            }}
                          >
                            {containerActionBusy
                              ? "Queuing…"
                              : `Confirm ${pendingContainerAction?.action ?? "action"}`}
                          </Button>
                          <Button
                            className="min-h-11"
                            size="sm"
                            variant="outline"
                            disabled={containerActionBusy !== null}
                            onClick={() => setPendingContainerAction(null)}
                          >
                            Cancel
                          </Button>
                        </div>
                      </div>
                    )}
                    <p className="text-xs text-muted-foreground">
                      Pull update downloads the image only; recreate the service through Compose to apply it.
                    </p>
                  </div>
                )}
              </div>
            ))}
            {(t.containers ?? []).length === 0 && (
              <p className="text-sm text-muted-foreground">None reported.</p>
            )}
          </CardContent>
        </Card>

        <Card className="min-w-0">
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
          <Card className="min-w-0 md:col-span-2">
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
                <div key={b.id} className="flex min-w-0 flex-col gap-2 rounded-md border p-2 text-sm sm:flex-row sm:items-center sm:justify-between">
                  <span className="min-w-0 break-words">
                    {new Date(b.requested_at).toLocaleString()} · {b.requested_by}
                  </span>
                  <span className="flex min-w-0 flex-wrap items-center gap-2 break-all text-xs text-muted-foreground">
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

        {commands !== null && (
          <Card className="min-w-0 md:col-span-2">
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>Remote actions</CardTitle>
              <Button
                size="sm"
                variant="outline"
                onClick={async () => {
                  setCommandMsg(null);
                  try {
                    await api.createCommand(id, "ping");
                    setCommandMsg("Ping queued in the audited command ledger.");
                    await loadCommands();
                  } catch (e: any) {
                    setCommandMsg(e.message);
                  }
                }}
              >
                Ping agent
              </Button>
            </CardHeader>
            <CardContent className="space-y-2">
              {commandMsg && <p className="text-xs text-muted-foreground">{commandMsg}</p>}
              {commands.length === 0 && (
                <p className="text-sm text-muted-foreground">No remote actions recorded.</p>
              )}
              {commands.map((command) => (
                <div key={command.id} className="flex min-w-0 flex-col gap-2 rounded-md border p-2 text-sm sm:flex-row sm:items-center sm:justify-between">
                  <span className="min-w-0 break-words">
                    {command.action} · {new Date(command.created_at).toLocaleString()} · {command.requested_by}
                  </span>
                  <span className="flex min-w-0 flex-wrap items-center gap-2 break-words text-xs text-muted-foreground">
                    {command.error ?? (command.attempts ? `${command.attempts} attempt(s)` : "waiting")}
                    {command.status === "AWAITING_APPROVAL" && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={async () => {
                          try {
                            await api.approveCommand(command.id);
                            setCommandMsg("Command approved and queued.");
                            await loadCommands();
                          } catch (e: any) {
                            setCommandMsg(e.message);
                          }
                        }}
                      >
                        Approve
                      </Button>
                    )}
                    <Badge
                      variant={
                        command.status === "SUCCEEDED"
                          ? "success"
                          : ["FAILED", "TIMED_OUT"].includes(command.status)
                            ? "destructive"
                            : "secondary"
                      }
                    >
                      {command.status.replaceAll("_", " ")}
                    </Badge>
                  </span>
                </div>
              ))}
              <p className="text-xs text-muted-foreground">
                Commands are tenant-scoped, policy checked, idempotent, and fully audited.
              </p>
            </CardContent>
          </Card>
        )}

        {emergencyEnabled && commands !== null && (
          <Card className="min-w-0 md:col-span-2">
            <CardHeader>
              <CardTitle>Break-glass diagnostics</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {!emergencyGrant ? (
                <div className="flex max-w-sm gap-2">
                  <Input
                    aria-label="Six-digit TOTP code"
                    inputMode="numeric"
                    maxLength={6}
                    placeholder="6-digit TOTP"
                    value={totp}
                    onChange={(event) => setTotp(event.target.value.replace(/\D/g, ""))}
                  />
                  <Button
                    variant="outline"
                    onClick={async () => {
                      try {
                        const session = await api.createEmergencySession(totp);
                        setEmergencyGrant(session.grant);
                        setTotp("");
                        setCommandMsg(`Emergency access unlocked until ${new Date(session.expires_at).toLocaleTimeString()}.`);
                      } catch (e: any) {
                        setCommandMsg(e.message);
                      }
                    }}
                  >
                    Unlock
                  </Button>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {DIAGNOSTICS.map(([value, label]) => (
                    <Button
                      key={value}
                      size="sm"
                      variant="outline"
                      onClick={async () => {
                        try {
                          await api.runDiagnostic(id, value, emergencyGrant);
                          setCommandMsg(`${label} diagnostic queued.`);
                          await loadCommands();
                        } catch (e: any) {
                          setCommandMsg(e.message);
                          if (String(e.message).includes("grant")) setEmergencyGrant(null);
                        }
                      }}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
              )}
              {commands
                .filter((command) => command.action === "diagnostic_run" && command.status === "SUCCEEDED")
                .slice(0, 1)
                .map((command) => (
                  <pre key={command.id} className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-3 text-xs">
                    {String(command.result?.output ?? "No output")}
                  </pre>
                ))}
              <p className="text-xs text-muted-foreground">
                Owner-only, TOTP-gated, short-lived access. Commands are fixed and read-only; output and access are audited.
              </p>
            </CardContent>
          </Card>
        )}

        {(t.proxmox_guests ?? []).length > 0 && (
          <Card className="min-w-0 md:col-span-2">
            <CardHeader>
              <CardTitle>Proxmox guests ({t.proxmox_guests.length})</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {t.proxmox_guests.map((guest: any) => (
                <div key={`${guest.node}-${guest.type}-${guest.vmid}`} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2 text-sm">
                  <span>
                    {guest.name || `Guest ${guest.vmid}`} · {guest.type.toUpperCase()} {guest.vmid} · {guest.node}
                  </span>
                  <span className="flex items-center gap-1">
                    <Badge variant={guest.status === "running" ? "success" : "secondary"}>
                      {guest.status}
                    </Badge>
                    {guest.status !== "running" ? (
                      <Button size="sm" variant="outline" onClick={() => runProxmoxAction("proxmox_start", guest)}>Start</Button>
                    ) : (
                      <>
                        <Button size="sm" variant="outline" onClick={() => runProxmoxAction("proxmox_reboot", guest)}>Reboot</Button>
                        <Button size="sm" variant="outline" onClick={() => runProxmoxAction("proxmox_shutdown", guest)}>Shutdown</Button>
                      </>
                    )}
                  </span>
                </div>
              ))}
              <p className="text-xs text-muted-foreground">
                Lifecycle actions are restricted by the agent-local guest allowlist. Proxmox credentials never leave the node.
              </p>
            </CardContent>
          </Card>
        )}

        <Card className="min-w-0 md:col-span-2">
          <CardHeader>
            <CardTitle>Disks ({(t.disks ?? []).length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {(t.disks ?? []).map((d: any) => (
              <div
                key={d.device_name}
                className="flex min-w-0 flex-col gap-2 rounded-md border p-2 text-sm sm:flex-row sm:items-center sm:justify-between"
              >
                <span className="min-w-0 break-all">
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
    </main>
  );
}
