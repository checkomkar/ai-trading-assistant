"""Add stored market history from one SQLite database to another without touching anything already there.

    python -m trader.scripts.merge_market_data --from data/trader.db --to data/market_slim.db --create
    python -m trader.scripts.merge_market_data --from /tmp/market_slim.db --to /app/data/trader.db

Copies instruments (matched by token), candles and option snapshots; skips ticks. Rows already present are left as
they are (INSERT OR IGNORE, never DELETE), so it is safe to run again and to run on a database that has accounts and
strategies. `--create` makes a new, empty target with just the market tables, a small file to upload to a server.
Run with the API stopped, or at least not writing candles."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

INSTRUMENT_COLS = ("token", "symbol", "underlying", "exchange", "instrument_type", "expiry", "strike", "option_type",
                   "lot_size", "tick_size", "is_active", "created_at", "updated_at")
CANDLE_COLS = ("timeframe", "ts", "open", "high", "low", "close", "volume", "oi")
SNAP_COLS = ("ts", "underlying", "expiry", "strike", "option_type", "spot", "ltp", "bid", "ask", "oi", "oi_change",
             "volume", "iv", "delta")
BATCH = 20000


def create_target(path: Path) -> None:
    from sqlalchemy import create_engine

    from trader.db.models import Base

    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng, tables=[Base.metadata.tables[t] for t in ("instruments", "candles", "option_snapshots")])
    eng.dispose()


def merge(src_path: Path, dst_path: Path) -> dict[str, int]:
    src, dst = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True), sqlite3.connect(dst_path)
    added = {"instruments": 0, "candles": 0, "option_snapshots": 0}
    try:
        cols = ",".join(INSTRUMENT_COLS)
        marks = ",".join("?" * len(INSTRUMENT_COLS))
        before = dst.execute("select count(*) from instruments").fetchone()[0]
        for row in src.execute(f"select {cols} from instruments"):
            dst.execute(f"insert or ignore into instruments ({cols}) values ({marks})", row)
        dst.commit()
        added["instruments"] = dst.execute("select count(*) from instruments").fetchone()[0] - before
        dst_ids = dict(dst.execute("select token, id from instruments").fetchall())
        src_tokens = dict(src.execute("select id, token from instruments").fetchall())
        remap = {sid: dst_ids[tok] for sid, tok in src_tokens.items() if tok in dst_ids}

        ccols = ",".join(("instrument_id",) + CANDLE_COLS)
        cmarks = ",".join("?" * (len(CANDLE_COLS) + 1))
        before = dst.execute("select count(*) from candles").fetchone()[0]
        cur = src.execute(f"select {ccols} from candles")
        while rows := cur.fetchmany(BATCH):
            dst.executemany(f"insert or ignore into candles ({ccols}) values ({cmarks})",
                            [(remap[r[0]], *r[1:]) for r in rows if r[0] in remap])
            dst.commit()
        added["candles"] = dst.execute("select count(*) from candles").fetchone()[0] - before

        # snapshots have no natural unique key in the table: add one while merging so re-runs do not duplicate
        dst.execute("create unique index if not exists ux_option_snapshots_merge on option_snapshots "
                    "(underlying, expiry, strike, option_type, ts)")
        scols = ",".join(SNAP_COLS)
        smarks = ",".join("?" * len(SNAP_COLS))
        before = dst.execute("select count(*) from option_snapshots").fetchone()[0]
        cur = src.execute(f"select {scols} from option_snapshots")
        while rows := cur.fetchmany(BATCH):
            dst.executemany(f"insert or ignore into option_snapshots ({scols}) values ({smarks})", rows)
            dst.commit()
        added["option_snapshots"] = dst.execute("select count(*) from option_snapshots").fetchone()[0] - before
    finally:
        src.close()
        dst.close()
    return added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True, type=Path)
    ap.add_argument("--to", dest="dst", required=True, type=Path)
    ap.add_argument("--create", action="store_true", help="create the target with only the market tables")
    a = ap.parse_args()
    if a.create:
        if a.dst.exists():
            raise SystemExit(f"{a.dst} already exists")
        create_target(a.dst)
    for table, n in merge(a.src, a.dst).items():
        print(f"{table}: {n} rows added")


if __name__ == "__main__":
    main()
