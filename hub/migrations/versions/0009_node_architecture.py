"""Store agent-reported node architecture.

Revision ID: 0009_node_architecture
Revises: 0008_emergency_access
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_node_architecture"
down_revision = "0008_emergency_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("arch", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "arch")
