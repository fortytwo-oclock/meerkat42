"use client";

import { useEffect, useState } from "react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { api, type EnterpriseFeatureItem, type LicenseInfo } from "@/lib/api";

export function LicenseModal({
  open,
  onClose,
  license,
  onApplied,
}: {
  open: boolean;
  onClose: () => void;
  license: LicenseInfo | null;
  onApplied: () => void;
}) {
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<EnterpriseFeatureItem[]>([]);

  useEffect(() => {
    if (!open) return;
    api
      .licenseFeatures()
      .then((res) => setCatalog(res.enterprise_features))
      .catch(() => setCatalog([]));
  }, [open, license?.active_tier]);

  const usage = license
    ? license.is_unlimited
      ? 0
      : Math.min(100, (license.current_nodes_count / Math.max(license.max_nodes, 1)) * 100)
    : 0;

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      await api.applyLicense(token.trim());
      setToken("");
      onApplied();
      onClose();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose}>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">License &amp; Edition</h2>
          {license && (
            <Badge variant={license.is_custom_license ? "default" : "secondary"}>
              {license.active_tier}
            </Badge>
          )}
        </div>

        {license && (
          <div className="space-y-1 text-sm text-muted-foreground">
            <div>{license.customer_name}</div>
            <div>
              Monitored devices: {license.current_nodes_count} /{" "}
              {license.is_unlimited ? "Unlimited (Open Core)" : license.max_nodes}
            </div>
            {!license.is_unlimited && (
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full bg-primary"
                  style={{ width: `${usage}%` }}
                />
              </div>
            )}
            {license.expires_at > 0 && (
              <div>Expires: {new Date(license.expires_at * 1000).toLocaleDateString()}</div>
            )}
          </div>
        )}

        {catalog.length > 0 && (
          <div className="space-y-1.5">
            <div className="text-xs font-medium">Enterprise capabilities</div>
            <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border p-2">
              {catalog.map((f) => (
                <div key={f.key} className="flex items-center justify-between gap-2 text-xs">
                  <span className="truncate" title={f.description}>
                    {f.name}
                  </span>
                  <Badge variant={f.enabled ? "default" : "secondary"}>
                    {f.enabled ? "Active" : "Enterprise"}
                  </Badge>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-2">
          <label className="text-xs font-medium">Paste signed Ed25519 license token</label>
          <Textarea
            rows={4}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="eyJhbGc...header.payload.signature"
          />
          {err && <p className="text-xs text-destructive">{err}</p>}
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!token.trim() || busy} onClick={submit}>
            {busy ? "Verifying…" : "Apply license"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
