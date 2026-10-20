"""Download candles for expired NIFTY / BANKNIFTY options from Fyers (PRD M2 item 6).

    # ATM +/- 5 strikes of the 2025-03-27 monthly NIFTY expiry, 5-minute bars
    python -m trader.scripts.fetch_expired_options --underlying NIFTY --expiry 2025-03-27 \\
        --monthly --atm 23500 --width 5 --timeframe 5m

    # explicit symbols
    python -m trader.scripts.fetch_expired_options --expiry 2025-11-04 \\
        --symbols NSE:NIFTY25N0425000CE,NSE:NIFTY25N0425000PE

Already-downloaded contracts are skipped, so re-running resumes. On the Standard plan only
FYERS_EXPIRED_INTRADAY_DAILY_LIMIT (default 10) intraday requests are made per day;
pass --prime to lift the limit if you subscribe to Fyers Prime.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from trader.brokers.fyers import expired
from trader.brokers.fyers.provider import FyersMarketDataProvider
from trader.config import get_settings
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.instruments.specs import get_spec
from trader.instruments.strikes import OptionType


def symbols_from_args(a: argparse.Namespace) -> list[str]:
    if a.symbols:
        return [s.strip() for s in a.symbols.split(",") if s.strip()]
    if a.atm is None:
        raise SystemExit("Give --symbols, or --underlying with --atm (and optionally --width)")
    step = get_spec(a.underlying).strike_interval
    strikes = [a.atm + i * step for i in range(-a.width, a.width + 1)]
    return [expired.build_symbol(a.underlying.upper(), a.expiry, k, ot, a.monthly)
            for k in strikes for ot in OptionType]


async def run(a: argparse.Namespace) -> None:
    settings = get_settings()
    provider = FyersMarketDataProvider(settings)
    rest = provider.rest()  # raises with a login hint if the token is missing/expired
    quota = expired.DailyQuota(settings.data_dir / "fyers" / "expired_quota.json",
                               0 if a.prime else settings.fyers_expired_intraday_daily_limit)
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)

    start = a.expiry - expired.MAX_RANGE + timedelta(days=1)
    for sym in symbols_from_args(a):
        inst = expired.expired_instrument(sym, a.expiry)
        async with sm() as s, s.begin():
            ids = await repo.upsert_instruments(s, [inst], is_active=False)
            if await repo.get_candles(s, ids[inst.token], a.timeframe, limit=1):
                print(f"  {inst.symbol}: already downloaded, skipped")
                continue
        intraday = a.timeframe != "1d"
        if intraday and quota.remaining() == 0:
            print(f"Daily quota of {quota.limit} intraday requests used. Re-run tomorrow (or use --prime).")
            break
        resp = await asyncio.to_thread(rest.fno_historical_data,
                                       expired.request(inst.token, a.timeframe, start, a.expiry))
        if intraday:
            quota.consume()
        try:
            candles = expired.parse_response(resp, inst.token, a.timeframe)
        except RuntimeError as exc:
            print(f"  {inst.symbol}: {exc}")
            continue
        async with sm() as s, s.begin():
            n = await repo.upsert_candles(s, candles, ids)
            await repo.record_event(s, "fyers", "expired.downloaded",
                                    {"symbol": inst.token, "timeframe": a.timeframe, "candles": n})
        print(f"  {inst.symbol}: {n} candles")
        await asyncio.sleep(0.4)
    await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--expiry", type=date.fromisoformat, required=True)
    p.add_argument("--underlying", default="NIFTY")
    p.add_argument("--symbols")
    p.add_argument("--atm", type=float)
    p.add_argument("--width", type=int, default=5)
    p.add_argument("--monthly", action="store_true", help="contract uses the monthly symbol format")
    p.add_argument("--timeframe", default="5m", choices=list(expired.RESOLUTION))
    p.add_argument("--prime", action="store_true", help="no daily quota (Fyers Prime subscribers)")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
