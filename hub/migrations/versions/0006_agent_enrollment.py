"""Tenant and group scoped agent enrollment tokens

Revision ID: 0006_agent_enrollment
Revises: 0005_license_delivery_outbox
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_agent_enrollment"
down_revision = "0005_license_delivery_outbox"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "agent_enrollment_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.String(36),
            sa.ForeignKey("client_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("expires_at", _TS),
        sa.Column("last_used_at", _TS),
        sa.Column("revoked_at", _TS),
    )
    op.create_index(
        "ix_agent_enrollment_tokens_tenant_id",
        "agent_enrollment_tokens",
        ["tenant_id"],
    )
    op.create_index(
        "ix_agent_enrollment_tokens_group_id",
        "agent_enrollment_tokens",
        ["group_id"],
    )
    op.create_index(
        "ix_agent_enrollment_tokens_token_hash",
        "agent_enrollment_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("agent_enrollment_tokens")
