"""Run Alembic migrations programmatically (used at API start-up and by CLI scripts)."""

from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from trader.config import BACKEND_DIR

INITIAL_REVISION = "59718d5a5a9f"


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["url"] = url
    return cfg


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    await engine.dispose()
    return names


def upgrade_db(url: str) -> None:
    """Upgrade to head. Databases created by the pre-Alembic `create_all` are stamped first.

    Must not be called from inside a running event loop (Alembic's env uses asyncio.run);
    from async code use `await asyncio.to_thread(upgrade_db, url)`.
    """
    cfg = _config(url)
    tables = asyncio.run(_tables(url))
    if "instruments" in tables and "alembic_version" not in tables:
        command.stamp(cfg, INITIAL_REVISION)
    command.upgrade(cfg, "head")
