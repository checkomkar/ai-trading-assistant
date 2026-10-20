"""Backfill index / futures / VIX candles from the configured broker (PRD M2 item 5).

    python -m trader.scripts.backfill_history --days 180
    python -m trader.scripts.backfill_history --days 365 --symbols NIFTY,NIFTY-FUT --timeframes 1m

Resumes from the last stored candle, so re-running only fetches what is missing.
Requires MARKET_DATA_PROVIDER=fyers and a valid token (python -m trader.brokers.fyers.login).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.market_data.factory import create_provider
from trader.market_data.service import MarketDataService


async def run(days: int, symbols: list[str], timeframes: list[str]) -> int:
    settings = get_settings()
    provider = create_provider(settings)
    if provider.is_simulated:
        raise SystemExit("MARKET_DATA_PROVIDER is 'simulated' — nothing to backfill. Set it to 'fyers'.")
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    md = MarketDataService(provider, create_sessionmaker(engine), settings)
    instruments = await provider.discover_instruments()
    md.registry.add_many(instruments)
    async with md.sessionmaker() as s, s.begin():  # type: ignore[union-attr]
        from trader.db import repository as repo

        md.token_to_id = await repo.upsert_instruments(s, instruments)

    end = md.now()
    start = end - timedelta(days=days)
    total = 0
    for sym in symbols:
        inst = md.candle_instrument(sym.upper())
        if inst is None:
            print(f"  {sym}: unknown symbol, skipped")
            continue
        for tf in timeframes:
            try:
                n = await md.backfill_instrument(inst, tf, start, end)
            except Exception as exc:  # noqa: BLE001 - report and carry on with the other symbols
                print(f"  {inst.symbol:<20} {tf:>3}: FAILED - {exc}")
                continue
            total += n
            print(f"  {inst.symbol:<20} {tf:>3}: {n} candles", flush=True)
    await engine.dispose()
    return total


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--symbols", default="NIFTY,BANKNIFTY,INDIAVIX,NIFTY-FUT,BANKNIFTY-FUT")
    p.add_argument("--timeframes", default="1m,5m",
                   help="comma list of 1m,2m,3m,5m,10m,15m,20m,30m,60m. 1m alone is enough: every "
                        "higher timeframe can be built from it")
    a = p.parse_args()
    total = asyncio.run(run(a.days, a.symbols.split(","), a.timeframes.split(",")))
    print(f"Done: {total} candles stored")


if __name__ == "__main__":
    main()
