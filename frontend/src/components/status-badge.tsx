import { Badge } from "@/components/ui/badge";

const MAP: Record<string, { variant: any; label: string }> = {
  ONLINE: { variant: "success", label: "Online" },
  STALE: { variant: "warning", label: "Stale" },
  OFFLINE: { variant: "destructive", label: "Offline" },
  UNKNOWN: { variant: "secondary", label: "Unknown" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = MAP[status] ?? MAP.UNKNOWN;
  return <Badge variant={s.variant}>{s.label}</Badge>;
}

export function severityVariant(sev: string): any {
  return (
    { CRITICAL: "destructive", HIGH: "destructive", WARNING: "warning", INFO: "secondary" }[
      sev
    ] ?? "secondary"
  );
}
