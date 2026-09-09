"""baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    op.create_table(
        "client_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("client_groups.id", ondelete="SET NULL")),
        sa.Column("created_at", _TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_group_tenant_slug"),
    )
    op.create_index("ix_client_groups_tenant_id", "client_groups", ["tenant_id"])
    op.create_index("ix_client_groups_slug", "client_groups", ["slug"])

    op.create_table(
        "nodes",
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("client_groups.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("os", sa.String(128)),
        sa.Column("kernel", sa.String(128)),
        sa.Column("agent_version", sa.String(64)),
        sa.Column("remote_ip", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("uptime_sec", sa.Integer(), nullable=False),
        sa.Column("first_seen", _TS, nullable=False),
        sa.Column("last_seen", _TS),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("last_telemetry", sa.JSON()),
        sa.Column("updated_at", _TS, nullable=False),
    )
    op.create_index("ix_nodes_group_id", "nodes", ["group_id"])

    op.create_table(
        "license_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_token", sa.Text()),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("customer_name", sa.String(200), nullable=False),
        sa.Column("issuer", sa.String(200)),
        sa.Column("subject", sa.String(200)),
        sa.Column("max_nodes", sa.Integer(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("white_label_logo", sa.String(500)),
        sa.Column("issued_at", _TS),
        sa.Column("expires_at", _TS),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("applied_at", _TS, nullable=False),
    )
    op.create_index("ix_license_records_tenant_id", "license_records", ["tenant_id"])
    op.create_index("ix_license_records_is_active", "license_records", ["is_active"])

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("client_groups.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("comparator", sa.String(4), nullable=False),
        sa.Column("threshold", sa.Float()),
        sa.Column("duration_sec", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_alert_rules_group_id", "alert_rules", ["group_id"])

    op.create_table(
        "dispatched_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("alert_rules.id", ondelete="SET NULL")),
        sa.Column("node_id", sa.String(128), sa.ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("resolved_at", _TS),
        sa.Column("acknowledged_at", _TS),
        sa.Column("acknowledged_by", sa.String(200)),
        sa.Column("dispatched_at", _TS),
    )
    op.create_index("ix_dispatched_alerts_rule_id", "dispatched_alerts", ["rule_id"])
    op.create_index("ix_dispatched_alerts_node_id", "dispatched_alerts", ["node_id"])
    op.create_index("ix_dispatched_alerts_state", "dispatched_alerts", ["state"])

    op.create_table(
        "metric_points",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.String(128), sa.ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
    )
    op.create_index("ix_metric_points_node_metric_ts", "metric_points", ["node_id", "metric", "ts"])

    op.create_table(
        "metric_rollups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.String(128), sa.ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("window", sa.String(8), nullable=False),
        sa.Column("bucket_start", _TS, nullable=False),
        sa.Column("avg_value", sa.Float(), nullable=False),
        sa.Column("min_value", sa.Float(), nullable=False),
        sa.Column("max_value", sa.Float(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_metric_rollups_lookup",
        "metric_rollups",
        ["node_id", "metric", "window", "bucket_start"],
    )


def downgrade() -> None:
    for t in (
        "metric_rollups",
        "metric_points",
        "dispatched_alerts",
        "alert_rules",
        "license_records",
        "nodes",
        "client_groups",
        "tenants",
    ):
        op.drop_table(t)
