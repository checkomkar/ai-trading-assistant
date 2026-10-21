"""strategy folders (grouping on the Strategies page)

Revision ID: f2c8d5b61a97
Revises: e7b3a9c25d14
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "f2c8d5b61a97"
down_revision = "e7b3a9c25d14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_folders",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_strategy_folders_owner", "strategy_folders", ["owner"])
    op.create_table(
        "strategy_folder_items",
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("strategy_name", sa.String(80), nullable=False),
        sa.Column("folder_id", sa.String(16), sa.ForeignKey("strategy_folders.id"), nullable=False),
        sa.PrimaryKeyConstraint("owner", "strategy_name"),
    )
    op.create_index("ix_strategy_folder_items_folder_id", "strategy_folder_items", ["folder_id"])


def downgrade() -> None:
    op.drop_table("strategy_folder_items")
    op.drop_table("strategy_folders")
