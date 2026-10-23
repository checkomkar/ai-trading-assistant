"""Download NIFTY 50 stock history from Fyers: daily and 1-minute candles (PRD §24.11).

    # What will happen (no requests, nothing written):
    python -m trader.scripts.fetch_equity_history --years 5 --dry-run

    # Daily + 1m for all NIFTY 50 stocks, 5 years (resumable: re-run after an interruption or the 06:00 expiry)
    python -m trader.scripts.fetch_equity_history --years 5

    # Daily only (what the Stocks backtests use today), or a few symbols
    python -m trader.scripts.fetch_equity_history --years 5 --timeframes 1d
    python -m trader.scripts.fetch_equity_history --years 5 --symbols RELIANCE,TCS

1d  re-fetches the whole range for every stock (a handful of requests each), so days stored earlier from Yahoo
    are replaced by Fyers' where both exist. Stamped 09:15 IST like every daily candle.
1m  walks back from each stock's first stored 1m candle to the start (or to where Fyers' history ends), then
    forward to now. About 25 requests per stock for 5 years. Nothing uses 1m stock candles yet: they are stored
    for intraday stock research later (PRD §24.12). Expect roughly 450k rows per stock (a few GB in total).

Needs MARKET_DATA_PROVIDER=fyers and today's login (python -m trader.brokers.fyers.login).
`merge_market_data --equities-only` still copies only the daily candles to a server unless asked for more.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from trader.config import get_settings
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.instruments import equities as eq
from trader.instruments.calendar import IST
from trader.market_data import deep_backfill, equity_daily
from trader.market_data.factory import create_provider

TIMEFRAMES = ("1d", "1m")


async def run(a: argparse.Namespace) -> None:
    base = get_settings()
    symbols = eq.expand(a.symbols.split(","))
    now = datetime.now(timezone.utc)
    start_day = now.astimezone(IST).date() - timedelta(days=a.years * 365)
    print(f"{len(symbols)} stocks, {', '.join(a.timeframes)}, from {start_day}" + (" (dry run)" if a.dry_run else ""))
    if a.dry_run:
        if "1d" in a.timeframes:
            print(f"  1d: about {len(symbols) * (a.years + 1)} requests")
        if "1m" in a.timeframes:
            print(f"  1m: about {len(symbols) * (a.years * 4 + 2)} requests of 90 days, roughly "
                  f"{len(symbols) * a.years * 250 * 375 / 1e6:.0f}M candles if Fyers has the full range")
        return
    # The provider clamps intraday requests to the plan's depth; Prime serves years, so lift it for this run only.
    settings = base.model_copy(update={"fyers_intraday_history_days":
                                       max(a.years * 366 + 7, base.fyers_intraday_history_days)})
    provider = create_provider(settings)
    if provider.is_simulated:
        raise SystemExit("MARKET_DATA_PROVIDER is 'simulated'. Set it to 'fyers' in .env.")
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    try:
        if "1d" in a.timeframes:
            print("\n== 1d ==")
            out = await equity_daily.refresh(provider, sm, symbols, a.years, full=True)
            print(f"  {sum(out['stored'].values())} daily candles for {len(out['stored'])} stocks; "
                  f"{len(out['failed'])} failed")
            for sym, why in out["failed"].items():
                print(f"    {sym}: {why}")
        if "1m" in a.timeframes:
            print("\n== 1m ==")
            async with sm() as s, s.begin():
                ids = await repo.upsert_instruments(s, [eq.instrument(x) for x in symbols])
            start = datetime.combine(start_day, datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
            failed: list[str] = []
            for sym in symbols:
                inst = eq.instrument(sym)
                iid = ids[inst.token]
                try:
                    deep = await deep_backfill.backfill_older(provider.history, sm, inst, iid, "1m", start, now,
                                                              say=(lambda _: None) if a.quiet else print)
                    fwd = await deep_backfill.backfill_newer(provider.history, sm, inst, iid, "1m", now)
                except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the rest
                    failed.append(sym)
                    print(f"  {sym:<12} FAILED: {str(exc)[:160]}")
                    continue
                first = f"{deep.earliest:%Y-%m-%d}" if deep.earliest else "none"
                where = "" if deep.reached_start else f" ({deep.note})"
                print(f"  {sym:<12} +{deep.stored} older, +{fwd} newer; first 1m candle {first}{where}")
            if failed:
                print(f"  {len(failed)} failed: {', '.join(failed)}. Re-run to retry; stored candles are kept.")
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--symbols", default="NIFTY50", help="comma list of symbols and/or the universe name NIFTY50")
    p.add_argument("--timeframes", default="1d,1m", help="1d, 1m or both")
    p.add_argument("--quiet", action="store_true", help="one line per stock instead of one per 90-day window")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    a.timeframes = [t.strip() for t in a.timeframes.split(",") if t.strip()]
    bad = [t for t in a.timeframes if t not in TIMEFRAMES]
    if bad:
        p.error(f"unsupported timeframe(s): {', '.join(bad)} (use 1d and/or 1m)")
    asyncio.run(run(a))


if __name__ == "__main__":
    main()
