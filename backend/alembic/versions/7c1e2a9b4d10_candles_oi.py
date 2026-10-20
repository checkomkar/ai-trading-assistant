"""candles.oi for derivative candles

Revision ID: 7c1e2a9b4d10
Revises: 59718d5a5a9f
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "7c1e2a9b4d10"
down_revision = "59718d5a5a9f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candles") as batch:
        batch.add_column(sa.Column("oi", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("candles") as batch:
        batch.drop_column("oi")
