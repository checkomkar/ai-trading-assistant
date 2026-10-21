"""Copy every table from one database to another, for moving hosts (for example SQLite -> PostgreSQL).

    python -m trader.scripts.copy_db --from sqlite+aiosqlite:///./data/trader.db --to postgresql+asyncpg://user:pw@host/db [--skip market_ticks]

The target must already be migrated (start the API once against it, or run `alembic upgrade head`) and empty.
Naive SQLite timestamps are UTC (CLAUDE.md), so they are stamped as UTC on the way in. Run with the API stopped."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timezone

from sqlalchemy import DateTime, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from trader.db.models import Base

BATCH = 2000


async def copy(src_url: str, dst_url: str, skip: set[str]) -> dict[str, int]:
    src, dst = create_async_engine(src_url), create_async_engine(dst_url)
    done: dict[str, int] = {}
    try:
        for table in Base.metadata.sorted_tables:  # parents before children
            if table.name in skip:
                continue
            async with dst.connect() as d:
                if (await d.execute(select(func.count()).select_from(table))).scalar_one() > 0:
                    raise SystemExit(f"{table.name} in the target is not empty; refusing to mix data.")
            dt_cols = [c.name for c in table.columns if isinstance(c.type, DateTime)]
            n = 0
            async with src.connect() as s:
                result = await s.stream(select(table))
                async for chunk in result.partitions(BATCH):
                    rows = [dict(r._mapping) for r in chunk]
                    for r in rows:
                        for c in dt_cols:
                            if r[c] is not None and r[c].tzinfo is None:
                                r[c] = r[c].replace(tzinfo=timezone.utc)
                    async with dst.begin() as d:
                        await d.execute(table.insert(), rows)
                    n += len(rows)
            done[table.name] = n
        if dst.dialect.name == "postgresql":  # copied explicit ids leave the sequences behind
            async with dst.begin() as d:
                for table in Base.metadata.sorted_tables:
                    pk = list(table.primary_key.columns)
                    if table.name in skip or len(pk) != 1 or not pk[0].autoincrement or pk[0].type.python_type is not int:
                        continue
                    await d.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}', '{pk[0].name}'), "
                                         f"COALESCE((SELECT MAX({pk[0].name}) FROM {table.name}), 1))"))
    finally:
        await src.dispose()
        await dst.dispose()
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--skip", nargs="*", default=[], help="tables to leave out, e.g. market_ticks (large and unused)")
    a = ap.parse_args()
    for table, n in asyncio.run(copy(a.src, a.dst, set(a.skip))).items():
        print(f"{table}: {n} rows")


if __name__ == "__main__":
    main()
