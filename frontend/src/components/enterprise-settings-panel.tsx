"use client";

import { useCallback, useEffect, useState } from "react";
import { Bell, Download, MapPin, ShieldCheck, Trash2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

export function EnterpriseSettingsPanel() {
  const [pushDevices, setPushDevices] = useState<
    {
      id: string;
      platform: string;
      device_name: string;
      principal: string;
      enabled: boolean;
      last_seen_at: string;
    }[]
  >([]);
  const [geofences, setGeofences] = useState<
    {
      id: string;
      name: string;
      lat: number;
      lon: number;
      radius_m: number;
      alert_on_exit: boolean;
      alert_on_enter: boolean;
      severity: string;
    }[]
  >([]);
  const [geofenceLicensed, setGeofenceLicensed] = useState(true);
  const [sso, setSso] = useState<{
    licensed: boolean;
    configured: boolean;
    enabled: boolean;
    provider_name: string;
    issuer_url: string | null;
  } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const [gfName, setGfName] = useState("");
  const [gfLat, setGfLat] = useState("37.7749");
  const [gfLon, setGfLon] = useState("-122.4194");
  const [gfRadius, setGfRadius] = useState("250");

  const load = useCallback(async () => {
    try {
      const p = await api.pushDevices();
      setPushDevices(p.devices);
    } catch {
      setPushDevices([]);
    }
    try {
      const g = await api.geofences();
      setGeofences(g.geofences);
      setGeofenceLicensed(true);
    } catch {
      setGeofenceLicensed(false);
    }
    try {
      setSso(await api.ssoConfig());
    } catch {
      setSso(null);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function addGeofence() {
    setMsg(null);
    try {
      await api.createGeofence({
        name: gfName.trim(),
        lat: Number(gfLat),
        lon: Number(gfLon),
        radius_m: Number(gfRadius),
        alert_on_exit: true,
        severity: "WARNING",
      });
      setGfName("");
      await load();
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Failed to create geofence");
    }
  }

  async function downloadAudit(format: "ndjson" | "json") {
    setMsg(null);
    try {
      const text = await api.exportAuditLogsText(format);
      const blob = new Blob([text], {
        type: format === "ndjson" ? "application/x-ndjson" : "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `meerkat42-audit.${format}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Audit export requires Enterprise license");
    }
  }

  return (
    <div className="space-y-6">
      {msg && (
        <p role="alert" className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
          {msg}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Bell className="h-4 w-4 text-muted-foreground" />
            Mobile &amp; Desktop Push Devices ({pushDevices.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {pushDevices.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No store apps registered for push notifications yet. Sign in from the Meerkat42 iOS, Android, macOS, or Windows app to register a device token.
            </p>
          ) : (
            pushDevices.map((d) => (
              <div
                key={d.id}
                className="flex items-center justify-between gap-2 rounded-md border p-2.5 text-xs"
              >
                <div>
                  <div className="font-medium">
                    {d.device_name}{" "}
                    <Badge variant="secondary" className="ml-1">
                      {d.platform}
                    </Badge>
                  </div>
                  <div className="text-muted-foreground">
                    {d.principal} · seen {new Date(d.last_seen_at).toLocaleString()}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    await api.deletePushDevice(d.id);
                    await load();
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <MapPin className="h-4 w-4 text-muted-foreground" />
            Device Tracking Geofences
          </CardTitle>
          <Badge variant={geofenceLicensed ? "default" : "secondary"}>
            {geofenceLicensed ? "Enterprise Active" : "Enterprise"}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {!geofenceLicensed ? (
            <p className="text-xs text-muted-foreground">
              Continuous device tracking &amp; geofencing alerts require an Enterprise license with the{" "}
              <code>geofencing</code> feature.
            </p>
          ) : (
            <>
              {geofences.map((z) => (
                <div
                  key={z.id}
                  className="flex items-center justify-between gap-2 rounded-md border p-2.5 text-xs"
                >
                  <div>
                    <span className="font-medium">{z.name}</span>{" "}
                    <Badge variant="secondary">{z.severity}</Badge>
                    <div className="text-muted-foreground">
                      {z.lat.toFixed(4)}, {z.lon.toFixed(4)} · radius {z.radius_m}m
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      await api.deleteGeofence(z.id);
                      await load();
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
              <div className="grid gap-2 pt-1 sm:grid-cols-5">
                <Input
                  placeholder="Zone name (e.g. HQ)"
                  value={gfName}
                  onChange={(e) => setGfName(e.target.value)}
                />
                <Input
                  placeholder="Lat"
                  type="number"
                  step="any"
                  value={gfLat}
                  onChange={(e) => setGfLat(e.target.value)}
                />
                <Input
                  placeholder="Lon"
                  type="number"
                  step="any"
                  value={gfLon}
                  onChange={(e) => setGfLon(e.target.value)}
                />
                <Input
                  placeholder="Radius (m)"
                  type="number"
                  value={gfRadius}
                  onChange={(e) => setGfRadius(e.target.value)}
                />
                <Button disabled={!gfName.trim()} onClick={addGeofence}>
                  Add zone
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-4 w-4 text-muted-foreground" />
            Enterprise SSO &amp; SIEM Compliance
          </CardTitle>
          <Badge variant={sso?.licensed ? "default" : "secondary"}>
            {sso?.licensed ? "Enterprise Active" : "Enterprise"}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-3 text-xs text-muted-foreground">
          <div>
            <strong>OIDC SSO:</strong>{" "}
            {sso?.enabled
              ? `${sso.provider_name} (${sso.issuer_url})`
              : sso?.licensed
                ? "Licensed — set OIDC_ISSUER_URL, OIDC_CLIENT_ID, and OIDC_REDIRECT_URI on the Hub."
                : "Requires Enterprise license (sso_oidc)."}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => downloadAudit("ndjson")}>
              <Download className="mr-1.5 h-3.5 w-3.5" />
              Export SIEM Audit (NDJSON)
            </Button>
            <Button size="sm" variant="outline" onClick={() => downloadAudit("json")}>
              <Download className="mr-1.5 h-3.5 w-3.5" />
              Export Audit (JSON)
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
