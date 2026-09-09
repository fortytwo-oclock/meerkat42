"""Short-lived emergency access grants

Revision ID: 0008_emergency_access
Revises: 0007_remote_commands
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_emergency_access"
down_revision = "0007_remote_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "emergency_access_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_emergency_access_grants_tenant_id", "emergency_access_grants", ["tenant_id"])
    op.create_index("ix_emergency_access_grants_token_hash", "emergency_access_grants", ["token_hash"], unique=True)
    op.create_index("ix_emergency_access_grants_expires_at", "emergency_access_grants", ["expires_at"])


def downgrade() -> None:
    op.drop_table("emergency_access_grants")
