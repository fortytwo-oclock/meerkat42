"""Stripe billing subscriptions and automated license issuance

Revision ID: 0004_stripe_billing
Revises: 0003_rbac_and_vault
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_stripe_billing"
down_revision = "0003_rbac_and_vault"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "billing_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stripe_customer_id", sa.String(255), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=False),
        sa.Column("stripe_product_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("customer_email", sa.String(320)),
        sa.Column("current_period_end", _TS),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("last_event_created", sa.Integer(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
    )
    op.create_index("ix_billing_subscriptions_tenant_id", "billing_subscriptions", ["tenant_id"], unique=True)
    op.create_index("ix_billing_subscriptions_stripe_customer_id", "billing_subscriptions", ["stripe_customer_id"])
    op.create_index("ix_billing_subscriptions_stripe_subscription_id", "billing_subscriptions", ["stripe_subscription_id"], unique=True)
    op.create_index("ix_billing_subscriptions_stripe_product_id", "billing_subscriptions", ["stripe_product_id"])
    op.create_index("ix_billing_subscriptions_status", "billing_subscriptions", ["status"])

    op.create_table(
        "stripe_webhook_events",
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("processed_at", _TS),
    )
    op.create_index("ix_stripe_webhook_events_event_type", "stripe_webhook_events", ["event_type"])
    op.create_index("ix_stripe_webhook_events_status", "stripe_webhook_events", ["status"])

    op.create_table(
        "issued_licenses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("billing_subscription_id", sa.String(36), sa.ForeignKey("billing_subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stripe_event_id", sa.String(255), sa.ForeignKey("stripe_webhook_events.event_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("raw_token", sa.Text(), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("issued_at", _TS, nullable=False),
        sa.Column("expires_at", _TS),
    )
    op.create_index("ix_issued_licenses_tenant_id", "issued_licenses", ["tenant_id"])
    op.create_index("ix_issued_licenses_billing_subscription_id", "issued_licenses", ["billing_subscription_id"])
    op.create_index("ix_issued_licenses_stripe_event_id", "issued_licenses", ["stripe_event_id"], unique=True)


def downgrade() -> None:
    op.drop_table("issued_licenses")
    op.drop_table("stripe_webhook_events")
    op.drop_table("billing_subscriptions")
