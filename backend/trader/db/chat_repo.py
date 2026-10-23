"""Storage for the assistant: conversations, uploaded knowledge and memory notes.

Everything is scoped by `owner` (always "local" until accounts exist, PRD §23), and all queries live here so adding
an account id later touches this file, not the routes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from trader.ai.knowledge import bm25_rank, chunk_text
from trader.db.models import ChatConversationRow, ChatMessageRow, KnowledgeChunkRow, KnowledgeDocRow, MemoryNoteRow

OWNER = "local"
MAX_MEMORY_NOTES = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---- conversations -------------------------------------------------------------------------------
async def create_conversation(s: AsyncSession, owner: str = OWNER, title: str = "New chat") -> ChatConversationRow:
    row = ChatConversationRow(id=_new_id(), owner=owner, title=title[:120], created_at=_now(), updated_at=_now())
    s.add(row)
    await s.flush()
    return row


async def list_conversations(s: AsyncSession, owner: str = OWNER) -> list[ChatConversationRow]:
    return list((await s.execute(select(ChatConversationRow).where(ChatConversationRow.owner == owner)
                                 .order_by(ChatConversationRow.updated_at.desc()).limit(100))).scalars())


async def get_conversation(s: AsyncSession, conv_id: str, owner: str = OWNER) -> ChatConversationRow | None:
    row = await s.get(ChatConversationRow, conv_id)
    return row if row is not None and row.owner == owner else None


async def delete_conversation(s: AsyncSession, conv_id: str, owner: str = OWNER) -> bool:
    if await get_conversation(s, conv_id, owner) is None:
        return False
    await s.execute(delete(ChatMessageRow).where(ChatMessageRow.conversation_id == conv_id))
    await s.execute(delete(ChatConversationRow).where(ChatConversationRow.id == conv_id))
    return True


async def messages(s: AsyncSession, conv_id: str) -> list[ChatMessageRow]:
    return list((await s.execute(select(ChatMessageRow).where(ChatMessageRow.conversation_id == conv_id)
                                 .order_by(ChatMessageRow.id))).scalars())


async def add_message(s: AsyncSession, conv: ChatConversationRow, role: str, content: str, cards: list | None = None,
                      doc_ids: list | None = None, provider: str | None = None) -> ChatMessageRow:
    row = ChatMessageRow(conversation_id=conv.id, role=role, content=content, cards=cards or [], doc_ids=doc_ids or [],
                         provider=provider, created_at=_now())
    s.add(row)
    conv.updated_at = _now()
    await s.flush()
    return row


# ---- knowledge -----------------------------------------------------------------------------------
async def add_doc(s: AsyncSession, *, filename: str, mime: str, kind: str, size: int, status: str, text: str,
                  summary: str, owner: str = OWNER) -> KnowledgeDocRow:
    doc = KnowledgeDocRow(id=_new_id(), owner=owner, filename=filename[:200], mime=mime[:96], kind=kind, size_bytes=size,
                          status=status, text=text, summary=summary, created_at=_now())
    s.add(doc)
    await s.flush()
    for i, c in enumerate(chunk_text(text)):
        s.add(KnowledgeChunkRow(doc_id=doc.id, owner=owner, idx=i, text=c))
    return doc


async def replace_doc_text(s: AsyncSession, doc_id: str, *, text: str, status: str, summary: str | None = None,
                           owner: str = OWNER) -> bool:
    """New text for an uploaded file (OCR finished): swap the stored text and re-chunk it for search."""
    doc = await s.get(KnowledgeDocRow, doc_id)
    if doc is None or doc.owner != owner:
        return False
    doc.text, doc.status = text, status
    if summary is not None:
        doc.summary = summary
    await s.execute(delete(KnowledgeChunkRow).where(KnowledgeChunkRow.doc_id == doc_id))
    for i, c in enumerate(chunk_text(text)):
        s.add(KnowledgeChunkRow(doc_id=doc_id, owner=owner, idx=i, text=c))
    return True


async def find_duplicate_doc(s: AsyncSession, filename: str, size: int, owner: str = OWNER) -> KnowledgeDocRow | None:
    """An earlier upload of the same file (same name and size) by this owner."""
    return (await s.execute(select(KnowledgeDocRow).where(
        KnowledgeDocRow.owner == owner, func.lower(KnowledgeDocRow.filename) == filename[:200].lower(),
        KnowledgeDocRow.size_bytes == size).order_by(KnowledgeDocRow.created_at).limit(1))).scalars().first()


async def list_docs(s: AsyncSession, owner: str = OWNER) -> list[KnowledgeDocRow]:
    return list((await s.execute(select(KnowledgeDocRow).where(KnowledgeDocRow.owner == owner)
                                 .order_by(KnowledgeDocRow.created_at.desc()))).scalars())


async def get_docs(s: AsyncSession, ids: list[str], owner: str = OWNER) -> list[KnowledgeDocRow]:
    if not ids:
        return []
    return list((await s.execute(select(KnowledgeDocRow).where(KnowledgeDocRow.id.in_(ids),
                                                               KnowledgeDocRow.owner == owner))).scalars())


async def delete_doc(s: AsyncSession, doc_id: str, owner: str = OWNER) -> bool:
    doc = await s.get(KnowledgeDocRow, doc_id)
    if doc is None or doc.owner != owner:
        return False
    await s.execute(delete(KnowledgeChunkRow).where(KnowledgeChunkRow.doc_id == doc_id))
    await s.execute(delete(KnowledgeDocRow).where(KnowledgeDocRow.id == doc_id))
    return True


async def search_knowledge(s: AsyncSession, query: str, k: int = 5, owner: str = OWNER,
                           doc_ids: list[str] | None = None) -> list[dict]:
    q = (select(KnowledgeChunkRow, KnowledgeDocRow.filename)
         .join(KnowledgeDocRow, KnowledgeDocRow.id == KnowledgeChunkRow.doc_id)
         .where(KnowledgeChunkRow.owner == owner))
    if doc_ids:
        q = q.where(KnowledgeChunkRow.doc_id.in_(doc_ids))
    rows = list((await s.execute(q)).all())
    ranked = bm25_rank(query, [r[0].text for r in rows], k)
    return [{"file": rows[i][1], "chunk": rows[i][0].idx, "score": round(sc, 2), "text": rows[i][0].text}
            for i, sc in ranked]


# ---- memory --------------------------------------------------------------------------------------
async def list_memory(s: AsyncSession, owner: str = OWNER) -> list[MemoryNoteRow]:
    return list((await s.execute(select(MemoryNoteRow).where(MemoryNoteRow.owner == owner)
                                 .order_by(MemoryNoteRow.id))).scalars())


async def add_memory(s: AsyncSession, text: str, source: str = "assistant", owner: str = OWNER) -> MemoryNoteRow | None:
    text = " ".join(text.split())[:500]
    if not text:
        return None
    existing = await list_memory(s, owner)
    if any(m.text.lower() == text.lower() for m in existing):
        return None
    if len(existing) >= MAX_MEMORY_NOTES:  # keep the newest: drop the oldest assistant-made note
        old = next((m for m in existing if m.source == "assistant"), existing[0])
        await s.execute(delete(MemoryNoteRow).where(MemoryNoteRow.id == old.id))
    row = MemoryNoteRow(owner=owner, text=text, source=source, created_at=_now())
    s.add(row)
    await s.flush()
    return row


async def delete_memory(s: AsyncSession, note_id: int, owner: str = OWNER) -> bool:
    res = await s.execute(delete(MemoryNoteRow).where(MemoryNoteRow.id == note_id, MemoryNoteRow.owner == owner))
    return res.rowcount > 0


async def count_docs(s: AsyncSession, owner: str = OWNER) -> int:
    return (await s.execute(select(func.count()).select_from(KnowledgeDocRow)
                            .where(KnowledgeDocRow.owner == owner))).scalar_one()
