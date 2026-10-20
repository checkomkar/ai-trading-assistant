"""Download expired NIFTY / BANKNIFTY option candles for a whole date range (PRD §20.1).

    # See what it would do. No login, no requests, nothing written.
    python -m trader.scripts.fetch_expired_bulk --start 2025-10-23 --end 2026-10-19 --dry-run

    # Standard plan: 10 requests a day, newest expiries first. Re-run daily; it resumes.
    python -m trader.scripts.fetch_expired_bulk --start 2025-10-23 --end 2026-10-19

    # Prime: no daily cap
    python -m trader.scripts.fetch_expired_bulk --start 2025-10-23 --end 2026-10-19 --prime

Needs the index candles for the range to be stored first (backfill_history): they decide which strikes
could have been at the money. Candles are stored at 1m; the backtester builds 5m from them exactly.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone

from trader.brokers.fyers import expired, expired_bulk as bulk
from trader.brokers.fyers.provider import FyersMarketDataProvider
from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.instruments.calendar import IST, get_calendar


async def run(a: argparse.Namespace) -> None:
    settings = get_settings()
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    fyers_dir = settings.data_dir / "fyers"
    ledger = bulk.EmptyLedger(fyers_dir / "expired_empty.json")
    today = datetime.now(timezone.utc).astimezone(IST).date()
    unds = ["NIFTY", "BANKNIFTY"] if a.underlying == "both" else [a.underlying]
    plan = await bulk.build_plan(sm, get_calendar(), unds, a.start, a.end, timeframe=a.timeframe, pad=a.pad,
                                 today=today, ledger=ledger, newest_first=not a.oldest_first)
    per_day = None if a.prime else settings.fyers_expired_intraday_daily_limit
    for line in bulk.summarise(plan, per_day):
        print(line)
    if a.dry_run or not plan.items:
        await engine.dispose()
        return
    rest = FyersMarketDataProvider(settings).rest()  # raises with a login hint if the token is missing/expired
    quota = expired.DailyQuota(fyers_dir / "expired_quota.json", 0 if a.prime else per_day or 10)
    res = await bulk.fetch(rest, sm, plan, timeframe=a.timeframe, quota=quota, ledger=ledger,
                           max_requests=a.max_requests, pause=a.pause)
    print(f"Done: {res.fetched} contracts ({res.candles} candles), {res.no_data} with no data, "
          f"{res.errors} errors." + (f" Stopped: {res.stopped}." if res.stopped else ""))
    await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--underlying", default="NIFTY", choices=["NIFTY", "BANKNIFTY", "both"])
    p.add_argument("--timeframe", default="1m", choices=list(expired.RESOLUTION))
    p.add_argument("--pad", type=int, default=1, help="extra strikes either side of the ones that could be ATM")
    p.add_argument("--prime", action="store_true", help="no daily quota (Fyers Prime subscribers)")
    p.add_argument("--max-requests", type=int, help="stop after this many requests (a budget for one sitting)")
    p.add_argument("--pause", type=float, default=0.4, help="seconds between requests")
    p.add_argument("--oldest-first", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="print the plan and exit; no login, no requests")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
