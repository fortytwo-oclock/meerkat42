"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { FleetMatrix } from "@/components/fleet-matrix";
import { AlertsPanel } from "@/components/alerts-panel";
import { LicenseModal } from "@/components/license-modal";
import {
  api,
  type AlertEvent,
  type Branding,
  type Group,
  type LicenseInfo,
} from "@/lib/api";

export default function DashboardPage() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [active, setActive] = useState<string | undefined>(undefined);
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);
  const [license, setLicense] = useState<LicenseInfo | null>(null);
  const [branding, setBranding] = useState<Branding | null>(null);
  const [showLicense, setShowLicense] = useState(false);

  const loadMeta = useCallback(async () => {
    const [g, l, b] = await Promise.all([
      api.groups(),
      api.license(),
      api.branding().catch(() => null),
    ]);
    setGroups(g.groups);
    setLicense(l);
    setBranding(b);
  }, []);

  const loadAlerts = useCallback(async () => {
    const { alerts } = await api.alerts(active);
    setAlerts(alerts);
  }, [active]);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    loadAlerts();
    const t = setInterval(loadAlerts, 10000);
    return () => clearInterval(t);
  }, [loadAlerts]);

  const logo = branding?.logo_url ?? license?.white_label_logo ?? null;
  const productName = branding?.product_name ?? "Control Plane";

  return (
    <div
      className="mx-auto max-w-7xl space-y-6 p-6"
      style={
        branding?.primary_color
          ? ({ "--primary": branding.primary_color } as Record<string, string>)
          : undefined
      }
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">
            {logo ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={logo} alt={productName} className="h-7" />
            ) : (
              productName
            )}
          </h1>
          <p className="text-sm text-muted-foreground">
            {license
              ? `${license.active_tier} · ${license.current_nodes_count}/${
                  license.is_unlimited ? "∞" : license.max_nodes
                } nodes`
              : "…"}
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/settings"
            className="inline-flex h-9 items-center rounded-md border border-input px-4 text-sm font-medium hover:bg-muted"
          >
            Settings
          </Link>
          <Button variant="outline" onClick={() => setShowLicense(true)}>
            Manage license
          </Button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setActive(undefined)}
          className={`rounded-full border px-3 py-1 text-xs ${
            active === undefined ? "bg-primary text-primary-foreground" : ""
          }`}
        >
          All clients
        </button>
        {groups.map((g) => (
          <button
            key={g.slug}
            onClick={() => setActive(g.slug)}
            className={`rounded-full border px-3 py-1 text-xs ${
              active === g.slug ? "bg-primary text-primary-foreground" : ""
            }`}
          >
            {g.name} ({g.node_count})
          </button>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <FleetMatrix group={active} />
        <AlertsPanel alerts={alerts} onChange={loadAlerts} />
      </div>

      <LicenseModal
        open={showLicense}
        onClose={() => setShowLicense(false)}
        license={license}
        onApplied={loadMeta}
      />
    </div>
  );
}
