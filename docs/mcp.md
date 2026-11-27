# OpenMeerkat MCP Server (AI Fleet Management)

Every OpenMeerkat Hub ships with a built-in **Model Context Protocol (MCP)** endpoint at `POST /api/v1/mcp` (Streamable HTTP / JSON-RPC 2.0) and a discovery manifest at `GET /api/v1/mcp/manifest`.

Connect Claude, Cursor, Antigravity, Codex, or Windsurf to ask natural-language questions about your servers, workstations, and mobile devices—and run audited, policy-validated maintenance actions using your existing RBAC API tokens.

## Security & RBAC

- **Same RBAC as REST:** Pass a standard Hub Bearer token (`Authorization: Bearer <token>`).
- **Viewer tokens** can only run read-only inspection tools (`get_fleet_summary`, `list_nodes`, `get_node_details`, `list_alerts`, `list_groups`) and are locked to their assigned group if group-scoped.
- **Admin / Owner tokens** can also create alert rules (`create_alert_rule`), queue policy-validated commands (`run_node_command`), check command output (`get_command_status`), and export sanitized workspaces (`export_workspace`).
- Every mutating MCP call is recorded in the Hub's immutable audit log under `mcp:<token-name>`.

## Remote Streamable HTTP Setup (Cursor, Antigravity, Windsurf, Claude)

```json
{
  "mcpServers": {
    "openmeerkat": {
      "url": "https://app.openmeerkat.com/api/v1/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_OPENMEERKAT_API_TOKEN"
      }
    }
  }
}
```

For self-hosted deployments, replace `https://app.openmeerkat.com` with your Hub URL (for example `https://meerkat.internal.example.com`).

## Local Stdio Bridge (for Stdio-Only Desktop Clients)

```json
{
  "mcpServers": {
    "openmeerkat": {
      "command": "python3",
      "args": ["/path/to/meerkat42/scripts/meerkat_mcp_stdio.py"],
      "env": {
        "MEERKAT_HUB_URL": "https://app.openmeerkat.com",
        "MEERKAT_API_TOKEN": "YOUR_OPENMEERKAT_API_TOKEN"
      }
    }
  }
}
```

## Available Tools

| Tool | Role | Purpose |
| --- | --- | --- |
| `get_fleet_summary` | Viewer+ | Overall online/offline counts, OS breakdown, active alerts, top CPU/RAM nodes |
| `list_nodes` | Viewer+ | Filter servers, workstations, and mobile devices by status, platform, class, or group |
| `get_node_details` | Viewer+ | Detailed telemetry, hardware info, uptime, labels, and active alerts for one machine |
| `list_alerts` | Viewer+ | Active firing/acknowledged alerts and configured rules |
| `list_groups` | Viewer+ | Logical device groups in the tenant |
| `create_alert_rule` | Admin+ | Create a threshold alert rule |
| `run_node_command` | Admin+ | Queue allowlisted Docker/Proxmox/ping remote commands with audit logging |
| `get_command_status` | Admin+ | Inspect status and output of a dispatched command |
| `export_workspace` | Admin+ | Generate a sanitized `openmeerkat_export_v1` migration bundle |

## Available Resources

- `openmeerkat://fleet/summary`
- `openmeerkat://nodes`
- `openmeerkat://alerts`
- `openmeerkat://tenant/routing`
