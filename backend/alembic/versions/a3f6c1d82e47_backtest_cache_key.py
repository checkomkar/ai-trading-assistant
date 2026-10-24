"""backtest_runs.cache_key: reuse a run when its inputs and data are unchanged

Revision ID: a3f6c1d82e47
Revises: f5c2d8e7a613
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa

revision = "a3f6c1d82e47"
down_revision = "f5c2d8e7a613"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("backtest_runs") as b:
        b.add_column(sa.Column("cache_key", sa.String(64)))
    op.create_index("ix_backtest_runs_cache_key", "backtest_runs", ["cache_key"])


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_cache_key", "backtest_runs")
    with op.batch_alter_table("backtest_runs") as b:
        b.drop_column("cache_key")
