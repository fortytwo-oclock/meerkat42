"""Add push_devices and geofence_zones tables for mobile push alerts and Enterprise geofencing.

Revision ID: 0012_push_and_geofences
Revises: 0011_device_classes_and_dynamic_metrics
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_push_and_geofences"
down_revision = "0011_device_classes_and_dynamic_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_label", sa.String(200), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("device_name", sa.String(200), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("device_token", sa.String(512), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "token_hash", name="uq_push_device_tenant_token"),
    )
    op.create_index("ix_push_devices_tenant_id", "push_devices", ["tenant_id"])
    op.create_index("ix_push_devices_platform", "push_devices", ["platform"])
    op.create_index("ix_push_devices_token_hash", "push_devices", ["token_hash"])

    op.create_table(
        "geofence_zones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("radius_m", sa.Float(), nullable=False),
        sa.Column("alert_on_exit", sa.Boolean(), nullable=False),
        sa.Column("alert_on_enter", sa.Boolean(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_geofence_tenant_name"),
    )
    op.create_index("ix_geofence_zones_tenant_id", "geofence_zones", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("geofence_zones")
    op.drop_table("push_devices")
