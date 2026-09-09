"""notification channels + alert-event dispatch bookkeeping

Revision ID: 0002_notification_channels
Revises: 0001_baseline
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_notification_channels"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.String(length=36),
            sa.ForeignKey("client_groups.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("min_severity", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_notification_channels_tenant_id", "notification_channels", ["tenant_id"]
    )

    with op.batch_alter_table("dispatched_alerts") as batch:
        batch.add_column(
            sa.Column("resolve_notified_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "suppressed", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("dispatched_alerts") as batch:
        batch.drop_column("suppressed")
        batch.drop_column("resolve_notified_at")
    op.drop_index("ix_notification_channels_tenant_id", "notification_channels")
    op.drop_table("notification_channels")
