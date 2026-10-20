"""paper_state (open positions, risk counters and kill switch survive a restart)

Revision ID: d4a7f1b93c58
Revises: c8d2e6a1f340
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "d4a7f1b93c58"
down_revision = "c8d2e6a1f340"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_state",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("trading_day", sa.String(length=10), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column("state", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("paper_state")
