"""Strategy folders. Owner-scoped (always "local" until accounts exist); all queries live here."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from trader.db.models import StrategyFolderItemRow, StrategyFolderRow, StrategyRow

OWNER = "local"
MAX_FOLDERS = 100


class FolderError(ValueError):
    """A request the user can fix: duplicate name, empty name, unknown folder or strategy."""


def _clean(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise FolderError("Give the folder a name.")
    if len(name) > 60:
        raise FolderError("Folder names can be at most 60 characters.")
    return name


async def _name_taken(s: AsyncSession, name: str, owner: str, except_id: str | None = None) -> bool:
    q = select(StrategyFolderRow.id).where(StrategyFolderRow.owner == owner,
                                           func.lower(StrategyFolderRow.name) == name.lower())
    return any(i != except_id for i in (await s.execute(q)).scalars())


async def list_folders(s: AsyncSession, owner: str = OWNER) -> list[dict]:
    folders = (await s.execute(select(StrategyFolderRow).where(StrategyFolderRow.owner == owner)
                               .order_by(StrategyFolderRow.position, StrategyFolderRow.created_at))).scalars().all()
    counts = dict((await s.execute(select(StrategyFolderItemRow.folder_id, func.count())
                                   .where(StrategyFolderItemRow.owner == owner)
                                   .group_by(StrategyFolderItemRow.folder_id))).all())
    return [{"id": f.id, "name": f.name, "position": f.position, "count": counts.get(f.id, 0)} for f in folders]


async def assignments(s: AsyncSession, owner: str = OWNER) -> dict[str, str]:
    """strategy name -> folder id."""
    rows = (await s.execute(select(StrategyFolderItemRow).where(StrategyFolderItemRow.owner == owner))).scalars()
    return {r.strategy_name: r.folder_id for r in rows}


async def create_folder(s: AsyncSession, name: str, owner: str = OWNER) -> StrategyFolderRow:
    name = _clean(name)
    if await _name_taken(s, name, owner):
        raise FolderError(f"You already have a folder called '{name}'.")
    n = (await s.execute(select(func.count()).select_from(StrategyFolderRow)
                         .where(StrategyFolderRow.owner == owner))).scalar_one()
    if n >= MAX_FOLDERS:
        raise FolderError(f"At most {MAX_FOLDERS} folders.")
    row = StrategyFolderRow(id=uuid.uuid4().hex[:12], owner=owner, name=name, position=n,
                            created_at=datetime.now(timezone.utc))
    s.add(row)
    await s.flush()
    return row


async def _get(s: AsyncSession, folder_id: str, owner: str) -> StrategyFolderRow:
    row = await s.get(StrategyFolderRow, folder_id)
    if row is None or row.owner != owner:
        raise FolderError("Folder not found.")
    return row


async def rename_folder(s: AsyncSession, folder_id: str, name: str, owner: str = OWNER) -> StrategyFolderRow:
    row = await _get(s, folder_id, owner)
    name = _clean(name)
    if await _name_taken(s, name, owner, except_id=folder_id):
        raise FolderError(f"You already have a folder called '{name}'.")
    row.name = name
    return row


async def delete_folder(s: AsyncSession, folder_id: str, owner: str = OWNER) -> None:
    """Deleting a folder never deletes strategies: they simply become ungrouped."""
    await _get(s, folder_id, owner)
    await s.execute(delete(StrategyFolderItemRow).where(StrategyFolderItemRow.folder_id == folder_id,
                                                        StrategyFolderItemRow.owner == owner))
    await s.execute(delete(StrategyFolderRow).where(StrategyFolderRow.id == folder_id))


async def move_strategy(s: AsyncSession, strategy_name: str, folder_id: str | None, owner: str = OWNER) -> None:
    """Put a strategy in a folder, or (folder_id=None) back to ungrouped."""
    exists = (await s.execute(select(StrategyRow.id).where(StrategyRow.name == strategy_name).limit(1))).first()
    if exists is None:
        raise FolderError("Strategy not found.")
    if folder_id is not None:
        await _get(s, folder_id, owner)
    item = await s.get(StrategyFolderItemRow, (owner, strategy_name))
    if folder_id is None:
        if item is not None:
            await s.delete(item)
    elif item is None:
        s.add(StrategyFolderItemRow(owner=owner, strategy_name=strategy_name, folder_id=folder_id))
    else:
        item.folder_id = folder_id
