from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from hub.db.base import Base, TZDateTime, created_at_col, utcnow, uuid_pk


class BillingSubscription(Base):
    """Stripe subscription state used to drive offline license issuance."""

    __tablename__ = "billing_subscriptions"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, index=True
    )
    stripe_customer_id: Mapped[str] = mapped_column(String(255), index=True)
    stripe_subscription_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True
    )
    stripe_product_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    customer_email: Mapped[Optional[str]] = mapped_column(String(320), default=None)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime, default=None
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False)
    last_event_created: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow
    )


class StripeWebhookEvent(Base):
    """Idempotency and audit record for every accepted Stripe event."""

    __tablename__ = "stripe_webhook_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    livemode: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="PROCESSING", index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    received_at: Mapped[datetime] = created_at_col()
    processed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)


class IssuedLicense(Base):
    """A license generated from a paid subscription event."""

    __tablename__ = "issued_licenses"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    billing_subscription_id: Mapped[str] = mapped_column(
        ForeignKey("billing_subscriptions.id", ondelete="CASCADE"), index=True
    )
    stripe_event_id: Mapped[str] = mapped_column(
        ForeignKey("stripe_webhook_events.event_id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    raw_token: Mapped[str] = mapped_column(Text)
    claims: Mapped[dict] = mapped_column(default=dict)
    issued_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    delivery_email: Mapped[Optional[str]] = mapped_column(String(320), default=None)
    delivery_status: Mapped[str] = mapped_column(
        String(16), default="SKIPPED", index=True
    )
    delivery_attempts: Mapped[int] = mapped_column(default=0)
    next_delivery_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime, default=None, index=True
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, default=None)
    delivery_error: Mapped[Optional[str]] = mapped_column(String(255), default=None)
