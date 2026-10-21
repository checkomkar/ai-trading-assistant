"""Google and phone-OTP sign-in: phone and google_sub on accounts, email and password optional, login_otps

Revision ID: c5a1e8d94b27
Revises: b3e8f1a47c05
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa

revision = "c5a1e8d94b27"
down_revision = "b3e8f1a47c05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as b:
        b.alter_column("email", existing_type=sa.String(254), nullable=True)
        b.alter_column("password_hash", existing_type=sa.String(256), nullable=True)
        b.add_column(sa.Column("phone", sa.String(20)))
        b.add_column(sa.Column("google_sub", sa.String(64)))
    op.create_index("ux_accounts_phone", "accounts", ["phone"], unique=True)
    op.create_index("ux_accounts_google_sub", "accounts", ["google_sub"], unique=True)
    op.create_table(
        "login_otps",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_login_otps_phone", "login_otps", ["phone"])


def downgrade() -> None:
    op.drop_table("login_otps")
    op.drop_index("ux_accounts_google_sub", "accounts")
    op.drop_index("ux_accounts_phone", "accounts")
    with op.batch_alter_table("accounts") as b:
        b.drop_column("google_sub")
        b.drop_column("phone")
