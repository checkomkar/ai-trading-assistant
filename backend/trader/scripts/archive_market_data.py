"""Keep a plain-file copy of stored broker market history (PRD §24.11).

    python -m trader.scripts.archive_market_data export                 # data/archive, only changed periods
    python -m trader.scripts.archive_market_data export --to D:/striktek-archive --full
    python -m trader.scripts.archive_market_data verify --to D:/striktek-archive
    python -m trader.scripts.archive_market_data restore --to D:/striktek-archive

export   gzip CSV per instrument type / index or stock / timeframe / month (daily: year) with a manifest of
         row counts, first and last candle and SHA-256 per file. Re-running rewrites only periods that changed.
verify   re-hashes every file against the manifest.
restore  inserts archived instruments and candles the database does not have yet; rows already there are
         left as they are. Verifies first and refuses a damaged archive.

Copy the folder somewhere off this machine as well (an external disk or cloud drive). Fyers data is licensed
to your account: keep the archive private.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.market_data import archive


async def run(a: argparse.Namespace) -> int:
    settings = get_settings()
    root: Path = a.to or settings.data_dir / "archive"
    if a.cmd == "verify":
        bad = archive.verify(root)
        files = len(archive.load_manifest(root)["files"])
        for line in bad:
            print(f"  {line}")
        print(f"{root}: {files} files, {'OK' if not bad else f'{len(bad)} problem(s)'}")
        return 1 if bad else 0
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    try:
        if a.cmd == "export":
            res = await archive.export(sm, root, full=a.full)
            print(f"{root}: {len(res.written)} files written ({res.rows} rows), {res.unchanged} unchanged")
        else:
            res = await archive.restore(sm, root)
            print(f"Restored from {root}: {res.instruments_added} instruments and {res.candles_added} candles added "
                  f"({res.files} files read)")
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=("export", "verify", "restore"))
    p.add_argument("--to", type=Path, help="archive folder (default: <data dir>/archive)")
    p.add_argument("--full", action="store_true", help="export: rewrite every file, not only changed periods")
    raise SystemExit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
