from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hub.models import BillingSubscription, IssuedLicense, StripeWebhookEvent


async def get_event(
    session: AsyncSession, event_id: str
) -> StripeWebhookEvent | None:
    return await session.get(StripeWebhookEvent, event_id)


async def get_subscription_for_tenant(
    session: AsyncSession, tenant_id: str
) -> BillingSubscription | None:
    rows = await session.execute(
        select(BillingSubscription).where(BillingSubscription.tenant_id == tenant_id)
    )
    return rows.scalar_one_or_none()


async def upsert_subscription(
    session: AsyncSession,
    *,
    tenant_id: str,
    customer_id: str,
    subscription_id: str,
    product_id: str,
    status: str,
    customer_email: str | None,
    current_period_end: datetime | None,
    cancel_at_period_end: bool,
    event_created: int,
) -> tuple[BillingSubscription, bool]:
    rows = await session.execute(
        select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == subscription_id
        )
    )
    subscription = rows.scalar_one_or_none()
    if subscription is None:
        rows = await session.execute(
            select(BillingSubscription).where(
                BillingSubscription.tenant_id == tenant_id
            )
        )
        subscription = rows.scalar_one_or_none()
    if subscription is None:
        subscription = BillingSubscription(tenant_id=tenant_id)
        session.add(subscription)
    elif subscription.tenant_id != tenant_id:
        raise ValueError("Stripe subscription is already assigned to another tenant")
    elif event_created < subscription.last_event_created:
        return subscription, False
    subscription.stripe_customer_id = customer_id
    subscription.stripe_subscription_id = subscription_id
    subscription.stripe_product_id = product_id
    subscription.status = status
    subscription.customer_email = customer_email
    subscription.current_period_end = current_period_end
    subscription.cancel_at_period_end = cancel_at_period_end
    subscription.last_event_created = event_created
    await session.flush()
    return subscription, True


async def latest_license(
    session: AsyncSession, tenant_id: str
) -> IssuedLicense | None:
    rows = await session.execute(
        select(IssuedLicense)
        .where(IssuedLicense.tenant_id == tenant_id)
        .order_by(IssuedLicense.issued_at.desc())
        .limit(1)
    )
    return rows.scalar_one_or_none()


async def latest_license_for_subscription(
    session: AsyncSession, billing_subscription_id: str
) -> IssuedLicense | None:
    rows = await session.execute(
        select(IssuedLicense)
        .where(IssuedLicense.billing_subscription_id == billing_subscription_id)
        .order_by(IssuedLicense.issued_at.desc())
        .limit(1)
    )
    return rows.scalar_one_or_none()


async def due_license_deliveries(
    session: AsyncSession, *, now: datetime, max_attempts: int, limit: int = 20
) -> list[IssuedLicense]:
    rows = await session.execute(
        select(IssuedLicense)
        .where(
            IssuedLicense.delivery_status.in_(("PENDING", "RETRY")),
            IssuedLicense.delivery_attempts < max_attempts,
            IssuedLicense.next_delivery_at.is_not(None),
            IssuedLicense.next_delivery_at <= now,
        )
        .order_by(IssuedLicense.next_delivery_at.asc())
        .limit(limit)
    )
    return list(rows.scalars())
