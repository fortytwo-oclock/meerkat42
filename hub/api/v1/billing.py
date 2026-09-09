from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hub.billing.gateway import StripeAPIError, StripeGateway
from hub.billing.service import SUPPORTED_SUBSCRIPTION_EVENTS, process_subscription_event
from hub.billing.stripe import StripeWebhookError, verify_signature
from hub.config import get_settings
from hub.db.session import get_session
from hub.deps import Principal, get_tenant_id, require_configured_owner
from hub.models import StripeWebhookEvent, Tenant
from hub.repositories import billing as billing_repo

router = APIRouter()


class CheckoutRequest(BaseModel):
    product_id: str
    customer_email: str | None = None


def _idempotency_key(value: str | None) -> str:
    key = (value or str(uuid.uuid4())).strip()
    if not key or len(key) > 255:
        raise HTTPException(status_code=422, detail="Invalid Idempotency-Key")
    return key


def _redirect_url(value: str) -> str:
    parsed = urlsplit(value)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if (
        (parsed.scheme != "https" and not local_http)
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(status_code=503, detail="Billing redirect URL is invalid")
    return value


def get_stripe_gateway() -> StripeGateway:
    return StripeGateway(get_settings().stripe_secret_key)


@router.post("/billing/stripe/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    settings = get_settings()
    payload = await request.body()
    if len(payload) > settings.stripe_webhook_max_bytes:
        raise HTTPException(status_code=413, detail="Stripe webhook payload too large")
    try:
        verify_signature(
            payload,
            stripe_signature or "",
            settings.stripe_webhook_secret,
            tolerance_seconds=settings.stripe_webhook_tolerance_seconds,
        )
        event = json.loads(payload)
    except (StripeWebhookError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    event_id = event.get("id")
    event_type = event.get("type")
    if not isinstance(event_id, str) or not event_id.startswith("evt_"):
        raise HTTPException(status_code=400, detail="Invalid Stripe event id")
    if not isinstance(event_type, str):
        raise HTTPException(status_code=400, detail="Invalid Stripe event type")

    existing = await billing_repo.get_event(session, event_id)
    if existing and existing.status != "FAILED":
        return {"received": True, "duplicate": True, "status": existing.status}
    if existing is None:
        event_row = StripeWebhookEvent(
            event_id=event_id,
            event_type=event_type,
            livemode=bool(event.get("livemode", False)),
        )
        session.add(event_row)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return {"received": True, "duplicate": True}
    else:
        event_row = existing
        event_row.status = "PROCESSING"
        event_row.error = None

    try:
        if event_type in SUPPORTED_SUBSCRIPTION_EVENTS:
            issued = await process_subscription_event(
                session, event_row, event, settings
            )
            event_row.status = "PROCESSED"
        else:
            issued = None
            event_row.status = "IGNORED"
        event_row.processed_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        failed = await billing_repo.get_event(session, event_id)
        if failed is None:
            failed = StripeWebhookEvent(
                event_id=event_id,
                event_type=event_type,
                livemode=bool(event.get("livemode", False)),
            )
            session.add(failed)
        failed.status = "FAILED"
        failed.error = str(exc)[:2000]
        failed.processed_at = datetime.now(timezone.utc)
        await session.commit()
        raise HTTPException(status_code=422, detail="Stripe event processing failed") from exc
    return {
        "received": True,
        "duplicate": False,
        "status": event_row.status,
        "license_issued": issued is not None,
    }


@router.get("/billing/plans")
async def billing_plans(
    _: Principal = Depends(require_configured_owner),
) -> dict:
    plans = []
    for product_id, plan in get_settings().stripe_product_plans.items():
        if not isinstance(plan, dict) or not plan.get("price_id"):
            continue
        plans.append(
            {
                "product_id": product_id,
                "tier": plan.get("tier"),
                "max_nodes": plan.get("max_nodes"),
                "features": plan.get("features", []),
            }
        )
    return {"plans": plans}


@router.post("/billing/checkout")
async def create_checkout(
    req: CheckoutRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_configured_owner),
    stripe: StripeGateway = Depends(get_stripe_gateway),
) -> dict:
    settings = get_settings()
    plan = settings.stripe_product_plans.get(req.product_id)
    if not isinstance(plan, dict) or not plan.get("price_id"):
        raise HTTPException(status_code=404, detail="Billing plan not found")
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    subscription = await billing_repo.get_subscription_for_tenant(session, tenant_id)
    try:
        stripe_session = await stripe.create_checkout_session(
            price_id=str(plan["price_id"]),
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            customer_name=tenant.name,
            customer_email=req.customer_email,
            stripe_customer_id=(subscription.stripe_customer_id if subscription else None),
            success_url=_redirect_url(settings.billing_success_url),
            cancel_url=_redirect_url(settings.billing_cancel_url),
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except StripeAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    url = stripe_session.get("url")
    if not isinstance(url, str) or not url.startswith("https://checkout.stripe.com/"):
        raise HTTPException(status_code=502, detail="Stripe returned an invalid Checkout URL")
    return {"url": url}


@router.post("/billing/portal")
async def create_portal(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_configured_owner),
    stripe: StripeGateway = Depends(get_stripe_gateway),
) -> dict:
    subscription = await billing_repo.get_subscription_for_tenant(session, tenant_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Billing subscription not found")
    try:
        stripe_session = await stripe.create_portal_session(
            customer_id=subscription.stripe_customer_id,
            return_url=_redirect_url(get_settings().billing_portal_return_url),
            idempotency_key=_idempotency_key(idempotency_key),
        )
    except StripeAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    url = stripe_session.get("url")
    if not isinstance(url, str) or not url.startswith("https://billing.stripe.com/"):
        raise HTTPException(status_code=502, detail="Stripe returned an invalid portal URL")
    return {"url": url}


@router.get("/billing/subscription")
async def billing_subscription(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_configured_owner),
) -> dict:
    row = await billing_repo.get_subscription_for_tenant(session, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Billing subscription not found")
    return {
        "stripe_customer_id": row.stripe_customer_id,
        "stripe_subscription_id": row.stripe_subscription_id,
        "stripe_product_id": row.stripe_product_id,
        "status": row.status,
        "current_period_end": row.current_period_end,
        "cancel_at_period_end": row.cancel_at_period_end,
    }


@router.get("/billing/license")
async def download_billing_license(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_configured_owner),
) -> dict:
    row = await billing_repo.latest_license(session, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No issued license found")
    return {
        "license_token": row.raw_token,
        "claims": row.claims,
        "issued_at": row.issued_at,
        "expires_at": row.expires_at,
        "delivery_email": row.delivery_email,
        "delivery_status": row.delivery_status,
        "delivery_attempts": row.delivery_attempts,
        "delivered_at": row.delivered_at,
        "delivery_error": row.delivery_error,
    }


@router.post("/billing/license/retry")
async def retry_billing_license_delivery(
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(get_tenant_id),
    _: Principal = Depends(require_configured_owner),
) -> dict:
    row = await billing_repo.latest_license(session, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No issued license found")
    if not row.delivery_email:
        raise HTTPException(status_code=409, detail="License has no delivery email")
    row.delivery_status = "PENDING"
    row.delivery_attempts = 0
    row.next_delivery_at = datetime.now(timezone.utc)
    row.delivered_at = None
    row.delivery_error = None
    await session.commit()
    return {"status": row.delivery_status}
