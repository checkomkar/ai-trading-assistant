"""backtest_runs

Revision ID: a3f9c2d71e05
Revises: 7c1e2a9b4d10
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "a3f9c2d71e05"
down_revision = "7c1e2a9b4d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("end", sa.Date(), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column("pricing_mode", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_runs_created_at", "backtest_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_created_at", table_name="backtest_runs")
    op.drop_table("backtest_runs")
