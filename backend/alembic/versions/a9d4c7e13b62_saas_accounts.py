"""SaaS: accounts, sessions, usage ledger, billing events; owner on strategies and backtest runs

Revision ID: a9d4c7e13b62
Revises: f2c8d5b61a97
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "a9d4c7e13b62"
down_revision = "f2c8d5b61a97"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("plan", sa.String(16), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("billing_customer", sa.String(64)),
        sa.Column("billing_subscription", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_accounts_billing_subscription", "accounts", ["billing_subscription"])
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(32), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_account_id", "auth_sessions", ["account_id"])
    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(128)),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_events_owner", "usage_events", ["owner"])
    op.create_index("ix_usage_owner_kind_ts", "usage_events", ["owner", "kind", "created_at"])
    op.create_table(
        "billing_events",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("account_id", sa.String(32)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Existing rows belong to the single local user. Batch mode so SQLite can rebuild the tables.
    with op.batch_alter_table("strategies") as b:
        b.add_column(sa.Column("owner", sa.String(32), nullable=False, server_default="local"))
        b.drop_index("ux_strategies_name_version")
        b.create_index("ix_strategies_owner", ["owner"])
        b.create_index("ux_strategies_owner_name_version", ["owner", "name", "version"], unique=True)
    with op.batch_alter_table("backtest_runs") as b:
        b.add_column(sa.Column("owner", sa.String(32), nullable=False, server_default="local"))
        b.create_index("ix_backtest_runs_owner", ["owner"])


def downgrade() -> None:
    with op.batch_alter_table("backtest_runs") as b:
        b.drop_index("ix_backtest_runs_owner")
        b.drop_column("owner")
    with op.batch_alter_table("strategies") as b:
        b.drop_index("ux_strategies_owner_name_version")
        b.drop_index("ix_strategies_owner")
        b.create_index("ux_strategies_name_version", ["name", "version"], unique=True)
        b.drop_column("owner")
    op.drop_table("billing_events")
    op.drop_table("usage_events")
    op.drop_table("auth_sessions")
    op.drop_table("accounts")
