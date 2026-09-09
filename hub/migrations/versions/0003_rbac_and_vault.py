"""RBAC api tokens + infrastructure vault backups

Revision ID: 0003_rbac_and_vault
Revises: 0002_notification_channels
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_rbac_and_vault"
down_revision = "0002_notification_channels"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("client_groups.id", ondelete="CASCADE")),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("last_used_at", _TS),
        sa.Column("revoked_at", _TS),
    )
    op.create_index("ix_api_tokens_tenant_id", "api_tokens", ["tenant_id"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)

    op.create_table(
        "config_backups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(128), sa.ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("command_id", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(500)),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("key_fingerprint", sa.String(64)),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("requested_at", _TS, nullable=False),
        sa.Column("completed_at", _TS),
    )
    op.create_index("ix_config_backups_tenant_id", "config_backups", ["tenant_id"])
    op.create_index("ix_config_backups_node_id", "config_backups", ["node_id"])
    op.create_index("ix_config_backups_command_id", "config_backups", ["command_id"])


def downgrade() -> None:
    op.drop_table("config_backups")
    op.drop_table("api_tokens")
