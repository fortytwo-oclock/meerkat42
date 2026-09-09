"""Durable remote commands and audit events

Revision ID: 0007_remote_commands
Revises: 0006_agent_enrollment
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_remote_commands"
down_revision = "0006_agent_enrollment"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "remote_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(128), sa.ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("destructive", sa.Boolean(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.String(200)),
        sa.Column("approved_at", _TS),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", _TS),
        sa.Column("dispatched_at", _TS),
        sa.Column("timeout_at", _TS),
        sa.Column("completed_at", _TS),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.String(500)),
        sa.Column("created_at", _TS, nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_command_tenant_idempotency"),
    )
    for column in ("tenant_id", "node_id", "action", "status", "next_attempt_at", "timeout_at"):
        op.create_index(f"ix_remote_commands_{column}", "remote_commands", [column])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
    )
    for column in ("tenant_id", "actor", "action", "target_id"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("remote_commands")
