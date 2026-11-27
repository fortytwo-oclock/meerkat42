"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

type RoutingInfo = Awaited<ReturnType<typeof api.tenantRouting>>;
type McpManifest = Awaited<ReturnType<typeof api.mcpManifest>>;

export function MigrationAndMcpPanel() {
  const [routing, setRouting] = useState<RoutingInfo | null>(null);
  const [manifest, setManifest] = useState<McpManifest | null>(null);
  const [slugInput, setSlugInput] = useState("");
  const [nameInput, setNameInput] = useState("");
  const [migrateUrl, setMigrateUrl] = useState("");
  const [migrateToken, setMigrateToken] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [copiedMcp, setCopiedMcp] = useState(false);

  const load = useCallback(async () => {
    try {
      const [r, m] = await Promise.all([api.tenantRouting(), api.mcpManifest()]);
      setRouting(r);
      setManifest(m);
      setSlugInput(r.tenant_slug);
      setNameInput(r.tenant_name);
    } catch {
      // Unauthenticated or unavailable
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSaveRouting = async () => {
    setBusy("routing");
    setNotice(null);
    try {
      const updated = await api.updateTenantRouting({
        slug: slugInput.trim() || undefined,
        name: nameInput.trim() || undefined,
      });
      setRouting(updated);
      setNotice({
        tone: "ok",
        text: updated.vanity_subdomain_url
          ? `Workspace subdomain updated to ${updated.vanity_subdomain_url}`
          : `Workspace updated (${updated.tenant_slug}).`,
      });
    } catch (e: unknown) {
      setNotice({ tone: "err", text: e instanceof Error ? e.message : "Failed to update routing." });
    } finally {
      setBusy(null);
    }
  };

  const handleExport = async () => {
    setBusy("export");
    setNotice(null);
    try {
      const bundle = await api.exportTenantWorkspace(false, true);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `openmeerkat-workspace-${routing?.tenant_slug || "export"}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setNotice({
        tone: "ok",
        text: "Exported portable openmeerkat_export_v1 JSON bundle (secrets redacted).",
      });
    } catch (e: unknown) {
      setNotice({ tone: "err", text: e instanceof Error ? e.message : "Export failed." });
    } finally {
      setBusy(null);
    }
  };

  const handleImportFile = async (file: File | null) => {
    if (!file) return;
    setBusy("import");
    setNotice(null);
    try {
      const raw = await file.text();
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const res = await api.importTenantWorkspace(parsed, true);
      const c = res.counts;
      setNotice({
        tone: "ok",
        text: `Imported ${c.groups_created ?? 0} groups, ${c.alert_rules_created ?? 0} alert rules, and ${c.nodes_imported ?? 0} nodes.${res.warnings.length ? ` (${res.warnings.length} warnings)` : ""}`,
      });
    } catch (e: unknown) {
      setNotice({ tone: "err", text: e instanceof Error ? e.message : "Import failed." });
    } finally {
      setBusy(null);
    }
  };

  const handleMigrateAgents = async () => {
    if (!migrateUrl.trim()) return;
    setBusy("migrate");
    setNotice(null);
    try {
      const res = await api.migrateAgentsToHub({
        hub_url: migrateUrl.trim(),
        enrollment_token: migrateToken.trim() || undefined,
        all_nodes: true,
      });
      setNotice({
        tone: "ok",
        text: `Queued migrate_hub command for ${res.queued_count} node(s) -> ${res.target_hub_url}`,
      });
      setMigrateToken("");
    } catch (e: unknown) {
      setNotice({ tone: "err", text: e instanceof Error ? e.message : "Agent migration failed." });
    } finally {
      setBusy(null);
    }
  };

  const mcpUrl =
    routing?.mcp_endpoint ||
    manifest?.endpoint ||
    (typeof window !== "undefined" ? `${window.location.origin}/api/v1/mcp` : "/api/v1/mcp");

  const mcpSnippet = JSON.stringify(
    {
      mcpServers: {
        openmeerkat: {
          url: mcpUrl,
          headers: { Authorization: "Bearer YOUR_OPENMEERKAT_API_TOKEN" },
        },
      },
    },
    null,
    2,
  );

  const handleCopyMcp = async () => {
    try {
      await navigator.clipboard.writeText(mcpSnippet);
      setCopiedMcp(true);
      window.setTimeout(() => setCopiedMcp(false), 1800);
    } catch {
      // Clipboard unavailable
    }
  };

  return (
    <div className="space-y-6">
      {notice && (
        <div
          className={`rounded-md border px-4 py-3 text-sm ${
            notice.tone === "ok"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              : "border-destructive/40 bg-destructive/10 text-destructive"
          }`}
        >
          {notice.text}
        </div>
      )}

      {/* Custom Subdomain & Self-Hosted Migration */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>Cloud Subdomain &amp; Self-Hosted Migration</CardTitle>
            {routing && (
              <Badge variant="outline">
                {routing.mode === "cloud" ? "Managed Cloud" : "Self-Hosted"}
              </Badge>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                Workspace Subdomain Slug
              </label>
              <Input
                value={slugInput}
                onChange={(e) => setSlugInput(e.target.value)}
                placeholder="acme-fleet"
              />
              {routing?.vanity_subdomain_url && (
                <p className="text-xs text-muted-foreground">
                  Vanity URL: <code className="font-mono">{routing.vanity_subdomain_url}</code>
                </p>
              )}
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                Workspace Display Name
              </label>
              <div className="flex gap-2">
                <Input
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  placeholder="My Fleet"
                />
                <Button
                  size="sm"
                  disabled={busy === "routing"}
                  onClick={handleSaveRouting}
                >
                  Save
                </Button>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t pt-4">
            <Button variant="outline" size="sm" disabled={busy === "export"} onClick={handleExport}>
              {busy === "export" ? "Exporting..." : "Export Workspace JSON"}
            </Button>
            <label className="inline-flex cursor-pointer items-center rounded-md border border-input bg-background px-3 py-1.5 text-sm font-medium hover:bg-accent">
              <span>{busy === "import" ? "Importing..." : "Import Workspace JSON"}</span>
              <input
                type="file"
                accept="application/json,.json"
                className="sr-only"
                disabled={busy === "import"}
                onChange={(e) => handleImportFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <span className="text-xs text-muted-foreground">
              Portable <code>openmeerkat_export_v1</code> archive for zero-lock-in migration.
            </span>
          </div>

          <div className="space-y-2 border-t pt-4">
            <p className="text-sm font-medium">Re-point Connected Agents to a Self-Hosted Hub</p>
            <div className="grid gap-2 sm:grid-cols-3">
              <Input
                value={migrateUrl}
                onChange={(e) => setMigrateUrl(e.target.value)}
                placeholder="https://meerkat.yourdomain.com"
              />
              <Input
                type="password"
                value={migrateToken}
                onChange={(e) => setMigrateToken(e.target.value)}
                placeholder="New Hub enrollment token (optional)"
              />
              <Button
                variant="outline"
                size="sm"
                disabled={!migrateUrl.trim() || busy === "migrate"}
                onClick={handleMigrateAgents}
              >
                {busy === "migrate" ? "Queuing..." : "Queue Fleet Migration"}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* AI / MCP Fleet Management */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>AI Assistant Integration (MCP Server)</CardTitle>
            <Badge variant={manifest?.enabled === false ? "secondary" : "default"}>
              {manifest?.enabled === false ? "Disabled" : "Streamable HTTP MCP"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Connect Claude, Cursor, Antigravity, Codex, or Windsurf directly to your Hub using any
            API token. Viewer tokens get read-only fleet inspection; Admin tokens can create alert
            rules and run audited commands.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {(manifest?.tools ?? []).map((tool) => (
              <Badge key={tool} variant="outline" className="font-mono text-xs">
                {tool}
              </Badge>
            ))}
          </div>
          <div className="relative rounded-md border bg-muted/50 p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-mono text-xs text-muted-foreground">{mcpUrl}</span>
              <Button variant="outline" size="sm" onClick={handleCopyMcp}>
                {copiedMcp ? "Copied" : "Copy MCP JSON"}
              </Button>
            </div>
            <pre className="overflow-x-auto font-mono text-xs">{mcpSnippet}</pre>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
