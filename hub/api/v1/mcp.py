"""Native Model Context Protocol (MCP) Streamable HTTP server for AI fleet management.

Exposes OpenMeerkat fleet visibility, alerting, audited remote commands, and
self-hosted migration exports over JSON-RPC 2.0 at `POST /api/v1/mcp`, using
standard Hub RBAC Bearer tokens and strict tenant / group isolation.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from hub.api.v1.migration import build_tenant_export_bundle, compute_fleet_summary
from hub.config import get_settings
from hub.db.session import get_session
from hub.deps import Principal, authenticate, get_tenant_id, viewer_scope
from hub.models import Role, Tenant
from hub.models.alert import AlertMetric, Comparator, Severity
from hub.remote.policy import CommandPolicyError, validate_command
from hub.repositories import alerts as alerts_repo
from hub.repositories import groups as groups_repo
from hub.repositories import nodes as nodes_repo
from hub.repositories import remote as remote_repo
from hub.tenant_routing import build_routing_info

router = APIRouter()

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SERVER_NAME = "openmeerkat-hub"
MCP_SERVER_VERSION = "2.0.0-beta"

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_fleet_summary",
        "description": (
            "Return an overall health report of your servers, computers, and mobile devices "
            "including online/offline counts, platform breakdown, active alerts, and top CPU/RAM nodes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_nodes",
        "description": "List monitored servers, workstations, and mobile devices with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["ONLINE", "STALE", "OFFLINE", "UNKNOWN"],
                    "description": "Filter by node liveness status.",
                },
                "platform": {
                    "type": "string",
                    "enum": ["linux", "windows", "macos", "ios", "android"],
                    "description": "Filter by OS platform.",
                },
                "device_class": {
                    "type": "string",
                    "enum": ["server", "workstation", "mobile", "iot", "container_host"],
                    "description": "Filter by device class.",
                },
                "group": {
                    "type": "string",
                    "description": "Filter by client group slug.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_node_details",
        "description": "Get detailed telemetry, OS/hardware info, uptime, labels, and active alerts for a specific node.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Unique node_id of the machine."},
            },
            "required": ["node_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_alerts",
        "description": "List active (firing or acknowledged) alerts and configured alert rules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_rules": {
                    "type": "boolean",
                    "description": "Include configured alert rules alongside active alerts.",
                    "default": True,
                },
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "create_alert_rule",
        "description": "Create a new threshold alert rule for the fleet or a specific group (requires admin role).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable alert rule name."},
                "metric": {
                    "type": "string",
                    "enum": [m.value for m in AlertMetric],
                },
                "comparator": {
                    "type": "string",
                    "enum": [c.value for c in Comparator],
                    "default": "gt",
                },
                "threshold": {"type": "number", "description": "Numeric threshold value."},
                "duration_sec": {"type": "integer", "minimum": 0, "default": 60},
                "severity": {
                    "type": "string",
                    "enum": [s.value for s in Severity],
                    "default": "WARNING",
                },
                "group_slug": {
                    "type": "string",
                    "description": "Optional group slug to scope this rule to.",
                },
            },
            "required": ["name", "metric"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "list_groups",
        "description": "List logical device groups in the tenant.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "run_node_command",
        "description": (
            "Queue an audited, policy-validated remote management command on a node "
            "(ping, docker_pull_image, docker_restart_container, docker_stop_container, "
            "docker_update_container, proxmox_start, proxmox_shutdown, proxmox_reboot). Requires admin role."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Target node_id."},
                "action": {
                    "type": "string",
                    "enum": [
                        "ping",
                        "docker_pull_image",
                        "docker_restart_container",
                        "docker_stop_container",
                        "docker_update_container",
                        "proxmox_start",
                        "proxmox_shutdown",
                        "proxmox_reboot",
                    ],
                },
                "payload": {
                    "type": "object",
                    "description": "Action-specific arguments (e.g. {\"container\": \"nginx\"}).",
                    "default": {},
                },
            },
            "required": ["node_id", "action"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "get_command_status",
        "description": "Check the execution status and result output of a previously queued remote command (requires admin role).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command_id": {"type": "string", "description": "Command UUID returned by run_node_command."},
            },
            "required": ["command_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "export_workspace",
        "description": "Generate a sanitized openmeerkat_export_v1 workspace bundle for self-hosted migration (requires admin role).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_telemetry": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include latest telemetry snapshot per node.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
]

MCP_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "openmeerkat://fleet/summary",
        "name": "Fleet Health Summary",
        "description": "Real-time summary of online/offline devices, platforms, alerts, and top CPU/RAM nodes.",
        "mimeType": "application/json",
    },
    {
        "uri": "openmeerkat://nodes",
        "name": "Fleet Nodes",
        "description": "All monitored servers, computers, and mobile devices in this workspace.",
        "mimeType": "application/json",
    },
    {
        "uri": "openmeerkat://alerts",
        "name": "Active Alerts",
        "description": "Currently firing or acknowledged alerts across the fleet.",
        "mimeType": "application/json",
    },
    {
        "uri": "openmeerkat://tenant/routing",
        "name": "Tenant Routing & Subdomain",
        "description": "Active Cloud URL, vanity subdomain, MCP endpoint, and self-hosted export links.",
        "mimeType": "application/json",
    },
]


def _rpc_result(req_id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id: Any, code: int, message: str, http_status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        status_code=http_status,
    )


def _tool_ok(data: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, default=str, indent=2)}],
        "isError": False,
    }


def _tool_err(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _serialize_node(n) -> dict[str, Any]:
    return {
        "node_id": n.node_id,
        "hostname": n.hostname,
        "status": getattr(n.status, "value", str(n.status)),
        "os": n.os,
        "platform": n.platform,
        "device_class": n.device_class,
        "arch": n.arch,
        "agent_version": n.agent_version,
        "uptime_sec": n.uptime_sec,
        "last_seen": n.last_seen.isoformat() if n.last_seen else None,
        "labels": n.labels or {},
        "last_telemetry": n.last_telemetry,
    }


@router.get("/mcp/manifest")
async def mcp_manifest(request: Request) -> dict:
    """Discovery metadata for connecting AI assistants to this Hub's MCP endpoint."""
    settings = get_settings()
    base_url = str(request.base_url).rstrip("/")
    return {
        "enabled": settings.mcp_enabled,
        "server": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        "protocol_version": MCP_PROTOCOL_VERSION,
        "transport": "streamable-http",
        "endpoint": f"{base_url}/api/v1/mcp",
        "authentication": "Bearer API token (Authorization: Bearer <token>)",
        "tools": [t["name"] for t in MCP_TOOLS],
        "resources": [r["uri"] for r in MCP_RESOURCES],
    }


async def _dispatch_tool(
    name: str,
    args: dict[str, Any],
    *,
    session: AsyncSession,
    tenant_id: str,
    principal: Principal,
    scope: str | None,
) -> dict[str, Any]:
    if name == "get_fleet_summary":
        summary = await compute_fleet_summary(session, tenant_id, group_slug=scope)
        return _tool_ok(summary)

    if name == "list_nodes":
        effective_group = scope or args.get("group")
        rows = await nodes_repo.list_for_tenant(
            session,
            tenant_id,
            group_slug=effective_group,
            device_class=args.get("device_class"),
            platform=args.get("platform"),
        )
        wanted_status = args.get("status")
        if wanted_status:
            rows = [r for r in rows if getattr(r.status, "value", str(r.status)) == wanted_status]
        return _tool_ok({"count": len(rows), "nodes": [_serialize_node(n) for n in rows]})

    if name == "get_node_details":
        node_id = str(args.get("node_id") or "").strip()
        if not node_id:
            return _tool_err("node_id is required")
        node = await nodes_repo.get_for_tenant(session, tenant_id, node_id)
        if node is None:
            return _tool_err(f"Node '{node_id}' not found")
        if scope:
            grp = await groups_repo.get_by_slug(session, tenant_id, scope)
            if grp is None or node.group_id != grp.id:
                return _tool_err(f"Node '{node_id}' not found")
        events = [
            ev
            for ev in await alerts_repo.active_events(session, tenant_id, group_slug=scope)
            if ev.node_id == node_id
        ]
        detail = _serialize_node(node)
        detail["hardware_info"] = node.hardware_info
        detail["kernel"] = node.kernel
        detail["active_alerts"] = [
            {
                "id": ev.id,
                "severity": getattr(ev.severity, "value", str(ev.severity)),
                "type": ev.type,
                "message": ev.message,
                "state": getattr(ev.state, "value", str(ev.state)),
                "started_at": ev.started_at.isoformat() if ev.started_at else None,
            }
            for ev in events
        ]
        return _tool_ok(detail)

    if name == "list_alerts":
        events = await alerts_repo.active_events(session, tenant_id, group_slug=scope)
        out: dict[str, Any] = {
            "active_alerts": [
                {
                    "id": ev.id,
                    "node_id": ev.node_id,
                    "severity": getattr(ev.severity, "value", str(ev.severity)),
                    "type": ev.type,
                    "subject": ev.subject,
                    "message": ev.message,
                    "value": ev.value,
                    "state": getattr(ev.state, "value", str(ev.state)),
                    "started_at": ev.started_at.isoformat() if ev.started_at else None,
                }
                for ev in events
            ]
        }
        if args.get("include_rules", True):
            rules = await alerts_repo.list_rules(session, tenant_id)
            out["rules"] = [
                {
                    "id": r.id,
                    "name": r.name,
                    "metric": getattr(r.metric, "value", str(r.metric)),
                    "comparator": getattr(r.comparator, "value", str(r.comparator)),
                    "threshold": r.threshold,
                    "duration_sec": r.duration_sec,
                    "severity": getattr(r.severity, "value", str(r.severity)),
                    "enabled": r.enabled,
                }
                for r in rules
            ]
        return _tool_ok(out)

    if name == "list_groups":
        groups = await groups_repo.list_groups(session, tenant_id)
        if scope:
            groups = [g for g in groups if g.slug == scope]
        return _tool_ok(
            {
                "groups": [
                    {
                        "id": g.id,
                        "slug": g.slug,
                        "name": g.name,
                        "description": g.description,
                    }
                    for g in groups
                ]
            }
        )

    if name == "create_alert_rule":
        if not principal.can(Role.ADMIN):
            return _tool_err("Forbidden: create_alert_rule requires admin role.")
        rule_name = str(args.get("name") or "").strip()
        metric_val = str(args.get("metric") or "")
        comp_val = str(args.get("comparator") or "gt")
        sev_val = str(args.get("severity") or "WARNING")
        if not rule_name:
            return _tool_err("name is required")
        try:
            metric = AlertMetric(metric_val)
            comp = Comparator(comp_val)
            sev = Severity(sev_val)
        except ValueError as exc:
            return _tool_err(f"Invalid enum value: {exc}")
        group_id = None
        if args.get("group_slug"):
            grp = await groups_repo.get_by_slug(session, tenant_id, str(args["group_slug"]))
            if grp is None:
                return _tool_err(f"Group '{args['group_slug']}' not found")
            group_id = grp.id
        rule = await alerts_repo.create_rule(
            session,
            group_id=group_id,
            name=rule_name[:200],
            metric=metric,
            comparator=comp,
            threshold=args.get("threshold"),
            duration_sec=max(0, int(args.get("duration_sec", 60))),
            severity=sev,
            enabled=True,
            is_builtin=False,
        )
        await remote_repo.append_audit(
            session,
            tenant_id=tenant_id,
            actor=f"mcp:{principal.label}",
            action="mcp.alert_rule.create",
            target_type="alert_rule",
            target_id=rule.id,
            details={"name": rule.name, "metric": metric.value},
        )
        await session.commit()
        return _tool_ok({"created": True, "id": rule.id, "name": rule.name, "metric": metric.value})

    if name == "run_node_command":
        if not principal.can(Role.ADMIN):
            return _tool_err("Forbidden: run_node_command requires admin role.")
        settings = get_settings()
        if not settings.remote_commands_enabled:
            return _tool_err("Remote commands are disabled on this Hub.")
        node_id = str(args.get("node_id") or "").strip()
        action = str(args.get("action") or "").strip()
        payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
        if await nodes_repo.get_for_tenant(session, tenant_id, node_id) is None:
            return _tool_err(f"Node '{node_id}' not found")
        if action in {"diagnostic_run", "migrate_hub"}:
            return _tool_err(f"Action '{action}' is not permitted via MCP tool.")
        try:
            policy, normalized = validate_command(action, payload)
        except CommandPolicyError as exc:
            return _tool_err(str(exc))
        approval_required = policy.destructive and settings.remote_command_two_person_approval
        row = await remote_repo.create(
            session,
            tenant_id=tenant_id,
            node_id=node_id,
            action=action,
            payload=normalized,
            requested_by=f"mcp:{principal.label}",
            destructive=policy.destructive,
            approval_required=approval_required,
        )
        await remote_repo.append_audit(
            session,
            tenant_id=tenant_id,
            actor=f"mcp:{principal.label}",
            action=f"mcp.command.{action}",
            target_type="node",
            target_id=node_id,
            details={"command_id": row.id, "payload": normalized},
        )
        await session.commit()
        return _tool_ok(
            {
                "command_id": row.id,
                "node_id": node_id,
                "action": action,
                "status": row.status,
                "approval_required": row.approval_required,
            }
        )

    if name == "get_command_status":
        if not principal.can(Role.ADMIN):
            return _tool_err("Forbidden: get_command_status requires admin role.")
        cmd_id = str(args.get("command_id") or "").strip()
        row = await remote_repo.get_for_tenant(session, tenant_id, cmd_id)
        if row is None or (row.action == "diagnostic_run" and principal.role != Role.OWNER):
            return _tool_err("Command not found")
        return _tool_ok(
            {
                "command_id": row.id,
                "node_id": row.node_id,
                "action": row.action,
                "status": row.status,
                "result": row.result,
                "error": row.error,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }
        )

    if name == "export_workspace":
        if not principal.can(Role.ADMIN):
            return _tool_err("Forbidden: export_workspace requires admin role.")
        bundle = await build_tenant_export_bundle(
            session,
            tenant_id,
            include_secrets=False,
            include_telemetry=bool(args.get("include_telemetry", False)),
        )
        return _tool_ok(bundle)

    return _tool_err(f"Unknown tool: {name}")


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(authenticate),
    scope: str | None = Depends(viewer_scope),
) -> Response:
    """Streamable HTTP JSON-RPC 2.0 Model Context Protocol endpoint."""
    settings = get_settings()
    if not settings.mcp_enabled:
        raise HTTPException(status_code=503, detail="MCP server is disabled on this Hub")

    try:
        body = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Parse error: invalid JSON", http_status=400)

    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "method" not in body:
        return _rpc_error(body.get("id") if isinstance(body, dict) else None, -32600, "Invalid Request", http_status=400)

    req_id = body.get("id")
    method = str(body.get("method"))
    params = body.get("params") if isinstance(body.get("params"), dict) else {}

    # JSON-RPC notification (no id)
    if method == "notifications/initialized":
        return Response(status_code=202)

    if method == "initialize":
        return _rpc_result(
            req_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {
                    "name": MCP_SERVER_NAME,
                    "version": MCP_SERVER_VERSION,
                },
                "instructions": (
                    "OpenMeerkat Hub MCP server. Inspect fleet health, servers, workstations, "
                    "mobile devices, alerts, and run policy-validated management commands."
                ),
            },
        )

    if method == "ping":
        return _rpc_result(req_id, {})

    if method == "tools/list":
        return _rpc_result(req_id, {"tools": MCP_TOOLS})

    if method == "tools/call":
        tool_name = str(params.get("name") or "")
        tool_args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not tool_name:
            return _rpc_error(req_id, -32602, "Missing required param: name")
        result = await _dispatch_tool(
            tool_name,
            tool_args,
            session=session,
            tenant_id=tenant_id,
            principal=principal,
            scope=scope,
        )
        return _rpc_result(req_id, result)

    if method == "resources/list":
        return _rpc_result(req_id, {"resources": MCP_RESOURCES})

    if method == "resources/read":
        uri = str(params.get("uri") or "")
        if uri == "openmeerkat://fleet/summary":
            data = await compute_fleet_summary(session, tenant_id, group_slug=scope)
        elif uri == "openmeerkat://nodes":
            rows = await nodes_repo.list_for_tenant(session, tenant_id, group_slug=scope)
            data = {"nodes": [_serialize_node(n) for n in rows]}
        elif uri == "openmeerkat://alerts":
            events = await alerts_repo.active_events(session, tenant_id, group_slug=scope)
            data = {
                "active_alerts": [
                    {
                        "id": ev.id,
                        "node_id": ev.node_id,
                        "severity": getattr(ev.severity, "value", str(ev.severity)),
                        "type": ev.type,
                        "message": ev.message,
                        "state": getattr(ev.state, "value", str(ev.state)),
                    }
                    for ev in events
                ]
            }
        elif uri == "openmeerkat://tenant/routing":
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                return _rpc_error(req_id, -32002, "Tenant not found")
            data = build_routing_info(
                tenant.slug, tenant.name, settings, request_base_url=str(request.base_url)
            )
        else:
            return _rpc_error(req_id, -32002, f"Resource not found: {uri}")

        return _rpc_result(
            req_id,
            {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(data, default=str, indent=2),
                    }
                ]
            },
        )

    return _rpc_error(req_id, -32601, f"Method not found: {method}")
