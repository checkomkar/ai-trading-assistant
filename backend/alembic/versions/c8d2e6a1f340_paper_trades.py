"""paper_trades (paper-trading journal)

Revision ID: c8d2e6a1f340
Revises: b5e1d4f08c22
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "c8d2e6a1f340"
down_revision = "b5e1d4f08c22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("trading_day", sa.String(length=10), nullable=False),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=80), nullable=False),
        sa.Column("strategy_version", sa.String(length=32), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("exit_reason", sa.String(length=48), nullable=False),
        sa.Column("gross_pnl", sa.Float(), nullable=False),
        sa.Column("charges", sa.Float(), nullable=False),
        sa.Column("net_pnl", sa.Float(), nullable=False),
        sa.Column("mae_pct", sa.Float(), nullable=True),
        sa.Column("mfe_pct", sa.Float(), nullable=True),
        sa.Column("minutes_held", sa.Float(), nullable=True),
        sa.Column("signal_reason", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("data_source", sa.String(length=16), nullable=False),
        sa.Column("option_pricing", sa.String(length=48), nullable=False),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_trades_correlation_id", "paper_trades", ["correlation_id"])
    op.create_index("ix_paper_trades_trading_day", "paper_trades", ["trading_day"])


def downgrade() -> None:
    op.drop_index("ix_paper_trades_trading_day", table_name="paper_trades")
    op.drop_index("ix_paper_trades_correlation_id", table_name="paper_trades")
    op.drop_table("paper_trades")
