"""equity_paper (stock paper portfolios)

Revision ID: d6e4b2f8a931
Revises: c5a1e8d94b27
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa

revision = "d6e4b2f8a931"
down_revision = "c5a1e8d94b27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_paper",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("strategy_id", sa.String(16), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("capital", sa.Float(), nullable=False),
        sa.Column("start_day", sa.Date(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_equity_paper_owner", "equity_paper", ["owner"])


def downgrade() -> None:
    op.drop_table("equity_paper")
