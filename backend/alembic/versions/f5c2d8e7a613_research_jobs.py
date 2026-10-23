"""background research jobs: sweeps, walk-forward, AI research (PRD §24.5-24.10)

Revision ID: f5c2d8e7a613
Revises: e1a7c3f09b52
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa

revision = "f5c2d8e7a613"
down_revision = "e1a7c3f09b52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_research_jobs_owner", "research_jobs", ["owner"])


def downgrade() -> None:
    op.drop_table("research_jobs")
