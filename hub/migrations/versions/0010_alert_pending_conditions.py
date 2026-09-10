"""Persist sustained alert-condition timers.

Revision ID: 0010_alert_pending_conditions
Revises: 0009_node_architecture
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_alert_pending_conditions"
down_revision = "0009_node_architecture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_pending_conditions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "rule_id",
            sa.String(36),
            sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.String(128),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "rule_id", "node_id", "type", "subject", name="uq_alert_pending_condition"
        ),
    )
    op.create_index(
        "ix_alert_pending_conditions_rule_id", "alert_pending_conditions", ["rule_id"]
    )
    op.create_index(
        "ix_alert_pending_conditions_node_id", "alert_pending_conditions", ["node_id"]
    )


def downgrade() -> None:
    op.drop_table("alert_pending_conditions")
