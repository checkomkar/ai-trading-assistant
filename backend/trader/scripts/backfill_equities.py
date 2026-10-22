"""Backfill (or refresh) daily candles for the NIFTY 50 stocks from the broker.

    python -m trader.scripts.backfill_equities --years 5
    python -m trader.scripts.backfill_equities --symbols RELIANCE,TCS --years 2

Resumes from the last stored day, so re-running only fetches what is missing. Needs a valid Fyers token
(python -m trader.brokers.fyers.login) and MARKET_DATA_PROVIDER=fyers.
"""

from __future__ import annotations

import argparse
import asyncio

from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.instruments import equities as eq
from trader.market_data import equity_daily
from trader.market_data.factory import create_provider


async def run(symbols: list[str], years: int) -> None:
    settings = get_settings()
    provider = create_provider(settings)
    if provider.is_simulated:
        raise SystemExit("MARKET_DATA_PROVIDER is 'simulated': nothing to fetch. Set it to 'fyers'.")
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    out = await equity_daily.refresh(provider, create_sessionmaker(engine), symbols, years)
    for sym, n in out["stored"].items():
        print(f"  {sym:<12} {n} candles")
    for sym, why in out["failed"].items():
        print(f"  {sym:<12} FAILED: {why}")
    print(f"Done: {sum(out['stored'].values())} candles stored, {len(out['failed'])} symbols failed")
    await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--symbols", default="NIFTY50", help="comma list of symbols and/or the universe name NIFTY50")
    a = p.parse_args()
    asyncio.run(run(eq.expand(a.symbols.split(",")), a.years))


if __name__ == "__main__":
    main()
