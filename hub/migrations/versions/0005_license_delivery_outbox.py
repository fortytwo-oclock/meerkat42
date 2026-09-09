"""Durable email outbox for issued licenses

Revision ID: 0005_license_delivery_outbox
Revises: 0004_stripe_billing
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_license_delivery_outbox"
down_revision = "0004_stripe_billing"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("issued_licenses") as batch:
        batch.add_column(sa.Column("delivery_email", sa.String(320)))
        batch.add_column(
            sa.Column(
                "delivery_status", sa.String(16), nullable=False, server_default="SKIPPED"
            )
        )
        batch.add_column(
            sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("next_delivery_at", _TS))
        batch.add_column(sa.Column("delivered_at", _TS))
        batch.add_column(sa.Column("delivery_error", sa.String(255)))
        batch.create_index("ix_issued_licenses_delivery_status", ["delivery_status"])
        batch.create_index("ix_issued_licenses_next_delivery_at", ["next_delivery_at"])


def downgrade() -> None:
    with op.batch_alter_table("issued_licenses") as batch:
        batch.drop_index("ix_issued_licenses_next_delivery_at")
        batch.drop_index("ix_issued_licenses_delivery_status")
        batch.drop_column("delivery_error")
        batch.drop_column("delivered_at")
        batch.drop_column("next_delivery_at")
        batch.drop_column("delivery_attempts")
        batch.drop_column("delivery_status")
        batch.drop_column("delivery_email")
