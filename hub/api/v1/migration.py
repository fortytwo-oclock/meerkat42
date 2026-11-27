"""Fleet summary, custom subdomain routing, and Cloud <-> Self-Hosted migration API."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.config import get_settings
from hub.db.session import get_session
from hub.deps import (
    Principal,
    authenticate,
    get_tenant_id,
    require_admin,
    require_owner,
    viewer_scope,
)
from hub.models import (
    AlertRule,
    ClientGroup,
    Node,
    NotificationChannel,
    Tenant,
)
from hub.models.alert import AlertMetric, Comparator, Severity
from hub.models.node import DeviceClass, NodeStatus, VALID_DEVICE_CLASSES, VALID_PLATFORMS
from hub.remote.policy import CommandPolicyError, validate_command
from hub.repositories import alerts as alerts_repo
from hub.repositories import channels as channels_repo
from hub.repositories import groups as groups_repo
from hub.repositories import nodes as nodes_repo
from hub.repositories import remote as remote_repo
from hub.repositories import tenants as tenants_repo
from hub.tenant_routing import build_routing_info, validate_tenant_slug

router = APIRouter()

EXPORT_FORMAT = "openmeerkat_export_v1"


def _telemetry_float(telemetry: dict | None, *keys: str) -> float | None:
    if not isinstance(telemetry, dict):
        return None
    for key in keys:
        val = telemetry.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


async def compute_fleet_summary(
    session: AsyncSession,
    tenant_id: str,
    *,
    group_slug: str | None = None,
) -> dict[str, Any]:
    """Compute a tenant- and group-scoped fleet health snapshot."""
    node_rows = await nodes_repo.list_for_tenant(session, tenant_id, group_slug=group_slug)
    active_alerts = await alerts_repo.active_events(session, tenant_id, group_slug=group_slug)
    group_rows = await groups_repo.list_groups(session, tenant_id)
    if group_slug:
        group_rows = [g for g in group_rows if g.slug == group_slug]

    by_status: Counter[str] = Counter()
    by_platform: Counter[str] = Counter()
    by_device_class: Counter[str] = Counter()
    resource_snapshots: list[dict[str, Any]] = []

    for n in node_rows:
        st = getattr(n.status, "value", str(n.status))
        by_status[st] += 1
        by_platform[n.platform or "unknown"] += 1
        by_device_class[n.device_class or DeviceClass.SERVER.value] += 1

        cpu = _telemetry_float(n.last_telemetry, "cpu_usage_pct", "cpu_pct", "cpu")
        ram = _telemetry_float(n.last_telemetry, "ram_usage_pct", "mem_pct", "memory_pct")
        if cpu is not None or ram is not None:
            resource_snapshots.append(
                {
                    "node_id": n.node_id,
                    "hostname": n.hostname,
                    "status": st,
                    "platform": n.platform,
                    "cpu_usage_pct": cpu,
                    "ram_usage_pct": ram,
                }
            )

    top_cpu = sorted(
        [r for r in resource_snapshots if r["cpu_usage_pct"] is not None],
        key=lambda r: r["cpu_usage_pct"],
        reverse=True,
    )[:5]
    top_ram = sorted(
        [r for r in resource_snapshots if r["ram_usage_pct"] is not None],
        key=lambda r: r["ram_usage_pct"],
        reverse=True,
    )[:5]

    alerts_by_severity: Counter[str] = Counter(
        getattr(ev.severity, "value", str(ev.severity)) for ev in active_alerts
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_nodes": len(node_rows),
        "total_groups": len(group_rows),
        "by_status": dict(by_status),
        "by_platform": dict(by_platform),
        "by_device_class": dict(by_device_class),
        "active_alerts_count": len(active_alerts),
        "active_alerts_by_severity": dict(alerts_by_severity),
        "top_cpu_nodes": top_cpu,
        "top_ram_nodes": top_ram,
    }


async def build_tenant_export_bundle(
    session: AsyncSession,
    tenant_id: str,
    *,
    include_secrets: bool = False,
    include_telemetry: bool = False,
) -> dict[str, Any]:
    """Serialize a tenant's workspace into a portable openmeerkat_export_v1 JSON bundle."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    groups = await groups_repo.list_groups(session, tenant_id)
    group_slug_by_id = {g.id: g.slug for g in groups}

    nodes = await nodes_repo.list_for_tenant(session, tenant_id)
    rules = await alerts_repo.list_rules(session, tenant_id)
    channels = await channels_repo.list_for_tenant(session, tenant_id)

    exported_groups = [
        {
            "slug": g.slug,
            "name": g.name,
            "description": g.description,
            "parent_slug": group_slug_by_id.get(g.parent_id) if g.parent_id else None,
        }
        for g in groups
    ]

    exported_rules = [
        {
            "name": r.name,
            "group_slug": group_slug_by_id.get(r.group_id) if r.group_id else None,
            "metric": getattr(r.metric, "value", str(r.metric)),
            "comparator": getattr(r.comparator, "value", str(r.comparator)),
            "threshold": r.threshold,
            "duration_sec": r.duration_sec,
            "severity": getattr(r.severity, "value", str(r.severity)),
            "enabled": r.enabled,
            "is_builtin": r.is_builtin,
        }
        for r in rules
        if not r.is_builtin
    ]

    exported_channels = []
    for ch in channels:
        if include_secrets:
            cfg = dict(ch.config or {})
        else:
            cfg = channels_repo.redact(ch)["config"]
        exported_channels.append(
            {
                "name": ch.name,
                "kind": ch.kind,
                "group_slug": group_slug_by_id.get(ch.group_id) if ch.group_id else None,
                "config": cfg,
                "secrets_redacted": not include_secrets,
                "min_severity": getattr(ch.min_severity, "value", str(ch.min_severity)),
                "enabled": ch.enabled,
            }
        )

    exported_nodes = []
    for n in nodes:
        entry: dict[str, Any] = {
            "node_id": n.node_id,
            "hostname": n.hostname,
            "group_slug": group_slug_by_id.get(n.group_id, "default"),
            "os": n.os,
            "platform": n.platform,
            "device_class": n.device_class,
            "arch": n.arch,
            "kernel": n.kernel,
            "agent_version": n.agent_version,
            "labels": dict(n.labels or {}),
        }
        if include_telemetry and n.last_telemetry is not None:
            entry["last_telemetry"] = n.last_telemetry
        exported_nodes.append(entry)

    return {
        "format": EXPORT_FORMAT,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "tenant": {
            "slug": tenant.slug,
            "name": tenant.name,
        },
        "groups": exported_groups,
        "alert_rules": exported_rules,
        "notification_channels": exported_channels,
        "nodes": exported_nodes,
    }


@router.get("/fleet/summary")
async def fleet_summary(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    scope: str | None = Depends(viewer_scope),
    _: Principal = Depends(authenticate),
) -> dict:
    """Return a high-level fleet health report for dashboards, scripts, and AI agents."""
    return await compute_fleet_summary(session, tenant_id, group_slug=scope)


@router.get("/tenant/routing")
async def get_tenant_routing(
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(authenticate),
) -> dict:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return build_routing_info(
        tenant.slug,
        tenant.name,
        get_settings(),
        request_base_url=str(request.base_url),
    )


class TenantRoutingUpdate(BaseModel):
    slug: str | None = Field(default=None, min_length=3, max_length=48)
    name: str | None = Field(default=None, min_length=1, max_length=200)


@router.patch("/tenant/routing")
async def update_tenant_routing(
    body: TenantRoutingUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_owner),
) -> dict:
    settings = get_settings()
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if body.slug is not None:
        try:
            new_slug = validate_tenant_slug(body.slug, settings)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if new_slug != tenant.slug:
            existing = await tenants_repo.get_by_slug(session, new_slug)
            if existing is not None and existing.id != tenant.id:
                raise HTTPException(status_code=409, detail="Subdomain slug is already taken")
            tenant.slug = new_slug

    if body.name is not None:
        tenant.name = body.name.strip()

    await remote_repo.append_audit(
        session,
        tenant_id=tenant_id,
        actor=principal.label,
        action="tenant.routing.update",
        target_type="tenant",
        target_id=tenant.id,
        details={"slug": tenant.slug, "name": tenant.name},
    )
    await session.commit()
    return build_routing_info(
        tenant.slug,
        tenant.name,
        settings,
        request_base_url=str(request.base_url),
    )


@router.get("/tenant/export")
async def export_tenant_workspace(
    include_secrets: bool = Query(
        default=False,
        description="Include unredacted notification channel credentials in the export bundle.",
    ),
    include_telemetry: bool = Query(
        default=False,
        description="Include latest denormalized telemetry snapshot per node.",
    ),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_admin),
) -> dict:
    """Export the authenticated tenant's configuration for self-hosted migration."""
    if include_secrets and principal.role.value != "owner":
        raise HTTPException(
            status_code=403,
            detail="include_secrets=true requires owner role",
        )
    bundle = await build_tenant_export_bundle(
        session,
        tenant_id,
        include_secrets=include_secrets,
        include_telemetry=include_telemetry,
    )
    await remote_repo.append_audit(
        session,
        tenant_id=tenant_id,
        actor=principal.label,
        action="tenant.export",
        target_type="tenant",
        target_id=tenant_id,
        details={
            "include_secrets": include_secrets,
            "include_telemetry": include_telemetry,
            "groups": len(bundle["groups"]),
            "nodes": len(bundle["nodes"]),
        },
    )
    await session.commit()
    return bundle


class WorkspaceImportRequest(BaseModel):
    bundle: dict[str, Any]
    import_nodes: bool = Field(
        default=True,
        description="Pre-register exported nodes into matching groups on this Hub.",
    )


@router.post("/tenant/import")
async def import_tenant_workspace(
    body: WorkspaceImportRequest,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_owner),
) -> dict:
    """Idempotently import an openmeerkat_export_v1 bundle into the authenticated tenant."""
    bundle = body.bundle
    if bundle.get("format") != EXPORT_FORMAT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported export format; expected '{EXPORT_FORMAT}'.",
        )

    warnings: list[str] = []
    counts = {
        "groups_created": 0,
        "alert_rules_created": 0,
        "channels_created": 0,
        "channels_skipped": 0,
        "nodes_imported": 0,
        "nodes_skipped": 0,
    }

    # 1. Groups
    existing_groups = {g.slug: g for g in await groups_repo.list_groups(session, tenant_id)}
    for raw_group in bundle.get("groups") or []:
        if not isinstance(raw_group, dict):
            continue
        slug = groups_repo.slugify(str(raw_group.get("slug") or raw_group.get("name") or ""))
        if slug in existing_groups:
            continue
        grp = ClientGroup(
            tenant_id=tenant_id,
            slug=slug,
            name=str(raw_group.get("name") or slug)[:200],
            description=(
                str(raw_group["description"])[:500]
                if raw_group.get("description")
                else None
            ),
        )
        session.add(grp)
        await session.flush()
        existing_groups[slug] = grp
        counts["groups_created"] += 1

    default_group = existing_groups.get(get_settings().default_group_slug)
    if default_group is None:
        default_group = await groups_repo.get_or_create(
            session, tenant_id, get_settings().default_group_slug
        )
        existing_groups[default_group.slug] = default_group

    # 2. Alert rules (non-builtin)
    existing_rules = {
        (r.name, r.group_id, getattr(r.metric, "value", str(r.metric)))
        for r in await alerts_repo.list_rules(session, tenant_id)
    }
    valid_metrics = {m.value for m in AlertMetric}
    valid_comps = {c.value for c in Comparator}
    valid_sevs = {s.value for s in Severity}

    for raw_rule in bundle.get("alert_rules") or []:
        if not isinstance(raw_rule, dict) or raw_rule.get("is_builtin"):
            continue
        metric_val = str(raw_rule.get("metric") or "")
        comp_val = str(raw_rule.get("comparator") or "gt")
        sev_val = str(raw_rule.get("severity") or "WARNING")
        if metric_val not in valid_metrics or comp_val not in valid_comps or sev_val not in valid_sevs:
            warnings.append(f"Skipped alert rule '{raw_rule.get('name', '?')}' with invalid enum value.")
            continue
        grp_slug = raw_rule.get("group_slug")
        group_id = existing_groups[grp_slug].id if grp_slug and grp_slug in existing_groups else None
        rule_name = str(raw_rule.get("name") or "Imported Rule")[:200]
        dedupe_key = (rule_name, group_id, metric_val)
        if dedupe_key in existing_rules:
            continue
        await alerts_repo.create_rule(
            session,
            group_id=group_id,
            name=rule_name,
            metric=AlertMetric(metric_val),
            comparator=Comparator(comp_val),
            threshold=raw_rule.get("threshold"),
            duration_sec=int(raw_rule.get("duration_sec") or 0),
            severity=Severity(sev_val),
            enabled=bool(raw_rule.get("enabled", True)),
            is_builtin=False,
        )
        existing_rules.add(dedupe_key)
        counts["alert_rules_created"] += 1

    # 3. Notification channels
    existing_channel_names = {
        (ch.name, ch.kind) for ch in await channels_repo.list_for_tenant(session, tenant_id)
    }
    for raw_ch in bundle.get("notification_channels") or []:
        if not isinstance(raw_ch, dict):
            continue
        ch_name = str(raw_ch.get("name") or "")[:200]
        ch_kind = str(raw_ch.get("kind") or "")[:16]
        if not ch_name or ch_kind not in {"discord", "slack", "smtp", "webhook"}:
            counts["channels_skipped"] += 1
            continue
        if raw_ch.get("secrets_redacted"):
            counts["channels_skipped"] += 1
            warnings.append(
                f"Skipped channel '{ch_name}' because credentials were redacted; re-add webhook/SMTP credentials or re-export with include_secrets=true."
            )
            continue
        if (ch_name, ch_kind) in existing_channel_names:
            counts["channels_skipped"] += 1
            continue
        grp_slug = raw_ch.get("group_slug")
        group_id = existing_groups[grp_slug].id if grp_slug and grp_slug in existing_groups else None
        sev_val = str(raw_ch.get("min_severity") or "WARNING")
        if sev_val not in valid_sevs:
            sev_val = "WARNING"
        await channels_repo.create(
            session,
            tenant_id,
            group_id=group_id,
            name=ch_name,
            kind=ch_kind,
            config=dict(raw_ch.get("config") or {}),
            min_severity=Severity(sev_val),
            enabled=bool(raw_ch.get("enabled", True)),
        )
        existing_channel_names.add((ch_name, ch_kind))
        counts["channels_created"] += 1

    # 4. Nodes metadata (optional pre-registration)
    if body.import_nodes:
        for raw_node in bundle.get("nodes") or []:
            if not isinstance(raw_node, dict):
                continue
            node_id = str(raw_node.get("node_id") or "").strip()[:128]
            if not node_id:
                continue
            existing_node = await session.get(Node, node_id)
            if existing_node is not None:
                counts["nodes_skipped"] += 1
                continue
            grp_slug = str(raw_node.get("group_slug") or "")
            target_group = existing_groups.get(grp_slug, default_group)
            platform = raw_node.get("platform")
            if platform not in VALID_PLATFORMS:
                platform = None
            device_class = raw_node.get("device_class")
            if device_class not in VALID_DEVICE_CLASSES:
                device_class = DeviceClass.SERVER.value
            node = Node(
                node_id=node_id,
                group_id=target_group.id,
                hostname=str(raw_node.get("hostname") or node_id)[:255],
                os=str(raw_node["os"])[:128] if raw_node.get("os") else None,
                platform=platform,
                device_class=device_class,
                arch=str(raw_node["arch"])[:32] if raw_node.get("arch") else None,
                kernel=str(raw_node["kernel"])[:128] if raw_node.get("kernel") else None,
                agent_version=(
                    str(raw_node["agent_version"])[:64]
                    if raw_node.get("agent_version")
                    else None
                ),
                status=NodeStatus.OFFLINE,
                labels=dict(raw_node.get("labels") or {}),
                last_telemetry=(
                    raw_node.get("last_telemetry")
                    if isinstance(raw_node.get("last_telemetry"), dict)
                    else None
                ),
            )
            session.add(node)
            counts["nodes_imported"] += 1

    await remote_repo.append_audit(
        session,
        tenant_id=tenant_id,
        actor=principal.label,
        action="tenant.import",
        target_type="tenant",
        target_id=tenant_id,
        details=counts,
    )
    await session.commit()
    return {"ok": True, "counts": counts, "warnings": warnings}


class MigrateAgentsRequest(BaseModel):
    hub_url: str = Field(min_length=8, max_length=512)
    enrollment_token: str | None = Field(default=None, max_length=512)
    verify_tls: bool = True
    node_ids: list[str] = Field(default_factory=list)
    all_nodes: bool = False


@router.post("/tenant/migrate-agents", status_code=status.HTTP_202_ACCEPTED)
async def migrate_agents_to_hub(
    body: MigrateAgentsRequest,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_owner),
) -> dict:
    """Queue audited migrate_hub commands to re-point fleet agents to a new Hub URL."""
    settings = get_settings()
    if not settings.remote_commands_enabled:
        raise HTTPException(status_code=503, detail="remote commands are disabled")

    raw_payload: dict[str, Any] = {
        "hub_url": body.hub_url,
        "verify_tls": body.verify_tls,
    }
    if body.enrollment_token:
        raw_payload["enrollment_token"] = body.enrollment_token

    try:
        policy, normalized = validate_command("migrate_hub", raw_payload)
    except CommandPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tenant_nodes = await nodes_repo.list_for_tenant(session, tenant_id)
    if body.all_nodes:
        targets = tenant_nodes
    else:
        wanted = set(body.node_ids)
        if not wanted:
            raise HTTPException(
                status_code=400,
                detail="Specify node_ids or set all_nodes=true.",
            )
        targets = [n for n in tenant_nodes if n.node_id in wanted]

    queued: list[dict[str, Any]] = []
    for node in targets:
        row = await remote_repo.create(
            session,
            tenant_id=tenant_id,
            node_id=node.node_id,
            action="migrate_hub",
            payload=normalized,
            requested_by=principal.label,
            destructive=policy.destructive,
            approval_required=False,
        )
        queued.append(
            {
                "command_id": row.id,
                "node_id": node.node_id,
                "hostname": node.hostname,
                "status": row.status,
            }
        )

    await remote_repo.append_audit(
        session,
        tenant_id=tenant_id,
        actor=principal.label,
        action="tenant.migrate_agents",
        target_type="tenant",
        target_id=tenant_id,
        details={"target_hub_url": normalized["hub_url"], "queued_count": len(queued)},
    )
    await session.commit()
    return {
        "ok": True,
        "target_hub_url": normalized["hub_url"],
        "queued_count": len(queued),
        "commands": queued,
    }
