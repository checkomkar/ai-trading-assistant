"""strategies (versioned Strategy Specs)

Revision ID: b5e1d4f08c22
Revises: a3f9c2d71e05
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "b5e1d4f08c22"
down_revision = "a3f9c2d71e05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("spec_hash", sa.String(length=16), nullable=False),
        sa.Column("english", sa.String(), nullable=False),
        sa.Column("source_text", sa.String(), nullable=True),
        sa.Column("assumptions", sa.JSON(), nullable=True),
        sa.Column("translator_model", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_strategies_name_version", "strategies", ["name", "version"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_strategies_name_version", table_name="strategies")
    op.drop_table("strategies")
