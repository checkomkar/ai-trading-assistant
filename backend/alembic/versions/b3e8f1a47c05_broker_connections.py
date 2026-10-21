"""per-account broker connections (encrypted API keys)

Revision ID: b3e8f1a47c05
Revises: a9d4c7e13b62
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "b3e8f1a47c05"
down_revision = "a9d4c7e13b62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_connections",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("broker", sa.String(16), nullable=False),
        sa.Column("public", sa.JSON(), nullable=False),
        sa.Column("secret_blob", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_broker_connections_owner", "broker_connections", ["owner"])
    op.create_index("ux_broker_owner_broker", "broker_connections", ["owner", "broker"], unique=True)


def downgrade() -> None:
    op.drop_table("broker_connections")
