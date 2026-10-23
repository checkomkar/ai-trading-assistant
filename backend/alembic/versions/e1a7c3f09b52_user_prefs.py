"""per-account UI preferences (chart indicators, PRD §24.3 B2.5)

Revision ID: e1a7c3f09b52
Revises: d6e4b2f8a931
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa

revision = "e1a7c3f09b52"
down_revision = "d6e4b2f8a931"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_prefs",
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("owner", "key"),
    )


def downgrade() -> None:
    op.drop_table("user_prefs")
