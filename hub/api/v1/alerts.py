from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.session import get_session
from hub.deps import (
    Principal,
    get_license_manager,
    get_tenant_id,
    require_admin,
    viewer_scope,
)
from hub.license_manager import LicenseManager
from hub.models.alert import AlertMetric, AlertState, Comparator, Severity
from hub.repositories import alerts as alerts_repo
from hub.repositories import groups as groups_repo
from hub.schemas import AlertRuleCreate

router = APIRouter()


@router.get("/alerts")
async def active_alerts(
    client_group: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    scope: str | None = Depends(viewer_scope),
) -> dict:
    if scope:  # viewer token locked to one client group
        client_group = scope
    events = await alerts_repo.active_events(session, tenant_id, group_slug=client_group)
    return {
        "alerts": [
            {
                "id": e.id,
                "severity": e.severity.value if hasattr(e.severity, "value") else e.severity,
                "node_id": e.node_id,
                "type": e.type,
                "subject": e.subject,
                "message": e.message,
                "state": e.state.value if hasattr(e.state, "value") else e.state,
                "value": e.value,
                "started_at": e.started_at,
                "acknowledged_by": e.acknowledged_by,
            }
            for e in events
        ],
        "total": len(events),
    }


@router.post("/alerts/{event_id}/ack")
async def ack_alert(
    event_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    principal: Principal = Depends(require_admin),
) -> dict:
    event = await alerts_repo.get_event_for_tenant(session, tenant_id, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if event.state != AlertState.RESOLVED:
        event.state = AlertState.ACKNOWLEDGED
        event.acknowledged_at = datetime.now(timezone.utc)
        event.acknowledged_by = principal.label  # authenticated principal, never client-supplied
    await session.commit()
    return {"id": event.id, "state": event.state.value}


@router.get("/alert-rules")
async def list_rules(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    rules = await alerts_repo.list_rules(session, tenant_id)
    return {
        "rules": [
            {
                "id": r.id,
                "name": r.name,
                "metric": r.metric.value if hasattr(r.metric, "value") else r.metric,
                "comparator": r.comparator.value if hasattr(r.comparator, "value") else r.comparator,
                "threshold": r.threshold,
                "duration_sec": r.duration_sec,
                "severity": r.severity.value if hasattr(r.severity, "value") else r.severity,
                "enabled": r.enabled,
                "is_builtin": r.is_builtin,
            }
            for r in rules
        ]
    }


@router.post("/alert-rules", status_code=201)
async def create_rule(
    req: AlertRuleCreate,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    lm: LicenseManager = Depends(get_license_manager),
    _: str = Depends(require_admin),
) -> dict:
    if lm.claims.get("tier") == "community" and not lm.has_feature("basic_alerts"):
        raise HTTPException(status_code=402, detail="Alerting not available in this tier")
    group_id = None
    if req.group_slug:
        group = await groups_repo.get_by_slug(session, tenant_id, req.group_slug)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        group_id = group.id
    try:
        rule = await alerts_repo.create_rule(
            session,
            group_id=group_id,
            name=req.name,
            metric=AlertMetric(req.metric),
            comparator=Comparator(req.comparator),
            threshold=req.threshold,
            duration_sec=req.duration_sec,
            severity=Severity(req.severity),
            enabled=req.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await session.commit()
    return {"id": rule.id, "name": rule.name}


@router.patch("/alert-rules/{rule_id}")
async def update_rule(
    rule_id: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: str = Depends(require_admin),
) -> dict:
    """Toggle / retune a rule — the dashboard's central alert-trigger controls."""
    rule = await alerts_repo.get_rule_for_tenant(session, tenant_id, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if "enabled" in body:
        rule.enabled = bool(body["enabled"])
    if "threshold" in body:
        rule.threshold = body["threshold"]
    if "severity" in body:
        try:
            rule.severity = Severity(body["severity"])
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid severity")
    if "duration_sec" in body:
        rule.duration_sec = int(body["duration_sec"])
    await session.commit()
    return {"id": rule.id, "enabled": rule.enabled}


@router.delete("/alert-rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: str = Depends(require_admin),
) -> dict:
    rule = await alerts_repo.get_rule_for_tenant(session, tenant_id, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.is_builtin:
        raise HTTPException(status_code=409, detail="Cannot delete a builtin rule; disable it instead")
    await session.delete(rule)
    await session.commit()
    return {"deleted": rule_id}
