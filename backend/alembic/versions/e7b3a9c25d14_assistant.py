"""assistant: conversations, messages, uploaded knowledge, memory notes

Revision ID: e7b3a9c25d14
Revises: d4a7f1b93c58
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "e7b3a9c25d14"
down_revision = "d4a7f1b93c58"
branch_labels = None
depends_on = None

_pk = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_conversations_owner", "chat_conversations", ["owner"])
    op.create_table(
        "chat_messages",
        sa.Column("id", _pk, primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.String(16), sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("cards", sa.JSON(), nullable=True),
        sa.Column("doc_ids", sa.JSON(), nullable=True),
        sa.Column("provider", sa.String(96), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"])
    op.create_table(
        "knowledge_docs",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("mime", sa.String(96), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_knowledge_docs_owner", "knowledge_docs", ["owner"])
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", _pk, primary_key=True, autoincrement=True),
        sa.Column("doc_id", sa.String(16), sa.ForeignKey("knowledge_docs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
    )
    op.create_index("ix_knowledge_chunks_doc_id", "knowledge_chunks", ["doc_id"])
    op.create_index("ix_knowledge_chunks_owner", "knowledge_chunks", ["owner"])
    op.create_table(
        "memory_notes",
        sa.Column("id", _pk, primary_key=True, autoincrement=True),
        sa.Column("owner", sa.String(32), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_memory_notes_owner", "memory_notes", ["owner"])


def downgrade() -> None:
    for t in ("memory_notes", "knowledge_chunks", "knowledge_docs", "chat_messages", "chat_conversations"):
        op.drop_table(t)
