"use client";

import { useState } from "react";
import { Check, Copy, Laptop, Monitor, Server, Smartphone } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, wsUrl, type Group } from "@/lib/api";

type TabKey = "linux" | "macos" | "windows" | "mobile";

export function AddDeviceModal({
  open,
  onClose,
  groups,
}: {
  open: boolean;
  onClose: () => void;
  groups: Group[];
}) {
  const [tab, setTab] = useState<TabKey>("linux");
  const [groupSlug, setGroupSlug] = useState(groups[0]?.slug ?? "default");
  const [token, setToken] = useState("");
  const [minting, setMinting] = useState(false);
  const [mintErr, setMintErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const streamUrl =
    typeof window !== "undefined"
      ? wsUrl("/api/v1/agent/stream")
      : "wss://hub.example.com/api/v1/agent/stream";
  const effectiveToken = token.trim() || "<YOUR_ENROLLMENT_TOKEN>";
  const effectiveGroup = groupSlug || "default";

  async function mintToken() {
    setMinting(true);
    setMintErr(null);
    try {
      const created = await api.createAgentToken({
        name: `${tab}-device-${new Date().toISOString().slice(0, 10)}`,
        group_slug: effectiveGroup,
      });
      setToken(created.token);
    } catch (e: unknown) {
      setMintErr(
        e instanceof Error
          ? e.message
          : "Unable to mint enrollment token (owner token required in Settings).",
      );
    } finally {
      setMinting(false);
    }
  }

  function commandForTab(): string {
    if (tab === "linux") {
      return [
        `# Native binary (Linux x86-64 / ARM64 / ARMv7):`,
        `export HUB_URL="${streamUrl}"`,
        `export AUTH_TOKEN="${effectiveToken}"`,
        `export CLIENT_GROUP="${effectiveGroup}"`,
        `export DEVICE_CLASS="server"`,
        `sudo -E ./control-plane-agent-linux-amd64 service install`,
      ].join("\n");
    }
    if (tab === "macos") {
      return [
        `# macOS Workstation (Apple Silicon or Intel):`,
        `export HUB_URL="${streamUrl}"`,
        `export AUTH_TOKEN="${effectiveToken}"`,
        `export CLIENT_GROUP="${effectiveGroup}"`,
        `export DEVICE_CLASS="workstation"`,
        `sudo -E ./control-plane-agent-darwin-arm64 service install`,
      ].join("\n");
    }
    if (tab === "windows") {
      return [
        `# Windows 10/11 or Windows Server (PowerShell as Administrator):`,
        `$env:HUB_URL="${streamUrl}"`,
        `$env:AUTH_TOKEN="${effectiveToken}"`,
        `$env:CLIENT_GROUP="${effectiveGroup}"`,
        `$env:DEVICE_CLASS="workstation"`,
        `.\\control-plane-agent-windows-amd64.exe service render`,
      ].join("\n");
    }
    const pairUri = `meerkat42://enroll?hub=${encodeURIComponent(streamUrl)}&token=${encodeURIComponent(effectiveToken)}&group=${encodeURIComponent(effectiveGroup)}`;
    return [
      `# Meerkat42 Mobile / Store App Pairing URI (iOS, Android, Mac, Windows):`,
      pairUri,
    ].join("\n");
  }

  async function copyCommand() {
    try {
      await navigator.clipboard.writeText(commandForTab());
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore clipboard error */
    }
  }

  const tabs: { key: TabKey; label: string; icon: typeof Server }[] = [
    { key: "linux", label: "Linux Server", icon: Server },
    { key: "macos", label: "macOS", icon: Laptop },
    { key: "windows", label: "Windows PC", icon: Monitor },
    { key: "mobile", label: "Mobile / Store App", icon: Smartphone },
  ];

  return (
    <Dialog open={open} onClose={onClose}>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold">Add Device</h2>
            <p className="text-xs text-muted-foreground">
              Connect a server, workstation, or mobile device to this Hub.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {tabs.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium ${
                tab === key ? "bg-primary text-primary-foreground" : "hover:bg-muted"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <label className="text-xs font-medium">Room / Client group</label>
            <select
              value={effectiveGroup}
              onChange={(e) => setGroupSlug(e.target.value)}
              className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              {groups.length === 0 ? (
                <option value="default">default</option>
              ) : (
                groups.map((g) => (
                  <option key={g.slug} value={g.slug}>
                    {g.name} ({g.slug})
                  </option>
                ))
              )}
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium">Enrollment token</label>
            <div className="flex gap-1.5">
              <Input
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="cpa_... or bootstrap token"
                className="h-9 text-xs"
              />
              <Button size="sm" variant="outline" onClick={mintToken} disabled={minting}>
                {minting ? "…" : "Mint"}
              </Button>
            </div>
            {mintErr && <p className="text-[11px] text-destructive">{mintErr}</p>}
          </div>
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium">Setup command</span>
            <Button size="sm" variant="outline" onClick={copyCommand}>
              {copied ? <Check className="mr-1 h-3.5 w-3.5" /> : <Copy className="mr-1 h-3.5 w-3.5" />}
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <pre className="overflow-x-auto rounded-md border bg-muted/50 p-3 text-xs leading-relaxed">
            {commandForTab()}
          </pre>
        </div>

        <div className="flex justify-end">
          <Button variant="outline" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
