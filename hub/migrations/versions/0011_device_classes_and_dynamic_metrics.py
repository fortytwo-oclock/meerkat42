"""Add multi-device classes, platform, hardware/location metadata, and wider dynamic metric keys.

Revision ID: 0011_device_classes_and_dynamic_metrics
Revises: 0010_alert_pending_conditions
"""
import sqlalchemy as sa
from alembic import op

from hub.db.base import JSONVariant

revision = "0011_device_classes_and_dynamic_metrics"
down_revision = "0010_alert_pending_conditions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("platform", sa.String(32), nullable=True))
    op.add_column(
        "nodes",
        sa.Column(
            "device_class",
            sa.String(32),
            nullable=False,
            server_default="server",
        ),
    )
    op.add_column("nodes", sa.Column("hardware_info", JSONVariant, nullable=True))
    op.add_column("nodes", sa.Column("last_location", JSONVariant, nullable=True))
    op.create_index("ix_nodes_platform", "nodes", ["platform"])
    op.create_index("ix_nodes_device_class", "nodes", ["device_class"])

    with op.batch_alter_table("metric_points") as batch_op:
        batch_op.alter_column(
            "metric",
            existing_type=sa.String(64),
            type_=sa.String(128),
            existing_nullable=False,
        )

    with op.batch_alter_table("metric_rollups") as batch_op:
        batch_op.alter_column(
            "metric",
            existing_type=sa.String(64),
            type_=sa.String(128),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("metric_rollups") as batch_op:
        batch_op.alter_column(
            "metric",
            existing_type=sa.String(128),
            type_=sa.String(64),
            existing_nullable=False,
        )

    with op.batch_alter_table("metric_points") as batch_op:
        batch_op.alter_column(
            "metric",
            existing_type=sa.String(128),
            type_=sa.String(64),
            existing_nullable=False,
        )

    op.drop_index("ix_nodes_device_class", table_name="nodes")
    op.drop_index("ix_nodes_platform", table_name="nodes")
    op.drop_column("nodes", "last_location")
    op.drop_column("nodes", "hardware_info")
    op.drop_column("nodes", "device_class")
    op.drop_column("nodes", "platform")
