from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from hub.billing.issuer import LicenseIssuanceError, issue_license
from hub.config import Settings
from hub.license_manager import COMMUNITY_CLAIMS
from hub.models import IssuedLicense, StripeWebhookEvent
from hub.repositories import billing as billing_repo
from hub.repositories import licenses as license_repo
from hub.repositories import tenants as tenants_repo

SUPPORTED_SUBSCRIPTION_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}
LICENSED_STATUSES = {"active", "trialing"}
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def _same_entitlements(
    issued: IssuedLicense,
    *,
    tenant_id: str,
    customer_name: str,
    plan: dict[str, Any],
    expires_at: datetime,
) -> bool:
    claims = issued.claims
    expected_expiry = int(expires_at.timestamp())
    return (
        claims.get("sub") == tenant_id
        and claims.get("customer_name") == customer_name
        and claims.get("tier") == str(plan.get("tier", "")).strip()
        and claims.get("max_nodes") == int(plan.get("max_nodes", 0))
        and claims.get("features") == [str(value) for value in plan.get("features", [])]
        and claims.get("exp") == expected_expiry
        and all(
            claims.get(name) == plan.get(name)
            for name in ("white_label_name", "white_label_logo", "white_label_color")
        )
    )


def _period_end(subscription: dict[str, Any]) -> datetime | None:
    raw = subscription.get("current_period_end")
    if not raw:
        items = ((subscription.get("items") or {}).get("data") or [])
        if items:
            raw = items[0].get("current_period_end")
    if not raw:
        return None
    return datetime.fromtimestamp(int(raw), tz=timezone.utc)


def _product_id(subscription: dict[str, Any]) -> str:
    items = ((subscription.get("items") or {}).get("data") or [])
    if not items:
        raise ValueError("Stripe subscription has no items")
    price = items[0].get("price") or {}
    product = price.get("product")
    if isinstance(product, dict):
        product = product.get("id")
    if not isinstance(product, str) or not product:
        raise ValueError("Stripe subscription item has no product id")
    return product


async def process_subscription_event(
    session: AsyncSession,
    event_row: StripeWebhookEvent,
    event: dict[str, Any],
    settings: Settings,
) -> IssuedLicense | None:
    subscription = ((event.get("data") or {}).get("object") or {})
    if not isinstance(subscription, dict):
        raise ValueError("Stripe event data.object must be an object")
    metadata = subscription.get("metadata") or {}
    tenant_slug = str(metadata.get("tenant_slug", "")).strip().lower()
    if not _SLUG.fullmatch(tenant_slug):
        raise ValueError("Subscription metadata.tenant_slug is required and invalid")

    subscription_id = str(subscription.get("id", ""))
    customer = subscription.get("customer")
    customer_id = customer.get("id") if isinstance(customer, dict) else customer
    if not subscription_id.startswith("sub_") or not isinstance(customer_id, str):
        raise ValueError("Stripe subscription id/customer is invalid")

    product_id = _product_id(subscription)
    status = str(subscription.get("status", "deleted"))
    customer_name = str(metadata.get("customer_name") or tenant_slug)
    customer_email = metadata.get("customer_email")
    tenant, _ = await tenants_repo.get_or_create_named(
        session, tenant_slug, customer_name
    )
    try:
        event_created = int(event.get("created", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Stripe event created timestamp is invalid") from exc
    billing_subscription, applied = await billing_repo.upsert_subscription(
        session,
        tenant_id=tenant.id,
        customer_id=customer_id,
        subscription_id=subscription_id,
        product_id=product_id,
        status=status,
        customer_email=str(customer_email) if customer_email else None,
        current_period_end=_period_end(subscription),
        cancel_at_period_end=bool(subscription.get("cancel_at_period_end", False)),
        event_created=event_created,
    )

    if not applied:
        return None
    if status not in LICENSED_STATUSES:
        await license_repo.set_active(
            session, tenant.id, raw_token=None, claims=COMMUNITY_CLAIMS
        )
        return None
    plan = settings.stripe_product_plans.get(product_id)
    if plan is None:
        raise ValueError(f"No license plan configured for Stripe product {product_id}")
    expires_at = billing_subscription.current_period_end
    if expires_at is None:
        raise ValueError("Active subscription has no current_period_end")
    existing_license = await billing_repo.latest_license_for_subscription(
        session, billing_subscription.id
    )
    if existing_license and _same_entitlements(
        existing_license,
        tenant_id=tenant.id,
        customer_name=customer_name,
        plan=plan,
        expires_at=expires_at,
    ):
        updated_email = str(customer_email).strip() if customer_email else None
        if updated_email and updated_email != existing_license.delivery_email:
            existing_license.delivery_email = updated_email
            existing_license.delivery_status = "PENDING"
            existing_license.delivery_attempts = 0
            existing_license.next_delivery_at = datetime.now(timezone.utc)
            existing_license.delivered_at = None
            existing_license.delivery_error = None
        return None
    try:
        token, claims = issue_license(
            settings.license_signing_private_key,
            subject=tenant.id,
            customer_name=customer_name,
            plan=plan,
            expires_at=expires_at,
            expected_public_key_b64=settings.ed25519_public_key,
        )
    except LicenseIssuanceError as exc:
        raise ValueError(str(exc)) from exc
    issued = IssuedLicense(
        tenant_id=tenant.id,
        billing_subscription_id=billing_subscription.id,
        stripe_event_id=event_row.event_id,
        raw_token=token,
        claims=claims,
        expires_at=expires_at,
        delivery_email=(str(customer_email).strip() if customer_email else None),
        delivery_status="PENDING" if customer_email else "SKIPPED",
        next_delivery_at=datetime.now(timezone.utc) if customer_email else None,
    )
    session.add(issued)
    await license_repo.set_active(
        session, tenant.id, raw_token=token, claims=claims
    )
    await session.flush()
    return issued
