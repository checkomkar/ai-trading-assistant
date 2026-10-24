"""Download everything a Fyers Prime subscription gives us, in the right order, then archive it (PRD §24.11).

    # What will happen (no requests, nothing written):
    python -m trader.scripts.fetch_prime_history --years 7 --dry-run

    # Everything: index/VIX/futures 1m, holidays, expired options 1m, NIFTY 50 daily, archive
    python -m trader.scripts.fetch_prime_history --years 7

    # One step at a time (each one resumes where it stopped):
    python -m trader.scripts.fetch_prime_history --years 7 --steps index
    python -m trader.scripts.fetch_prime_history --years 7 --steps holidays,options
    python -m trader.scripts.fetch_prime_history --steps archive

Steps, in order (each is safe to interrupt and re-run):
  index     NIFTY, BANKNIFTY, INDIAVIX and both near-month futures (continuous) at 1m, walking back from the
            first stored candle until --years or until Fyers' history ends, then forward to now.
  holidays  weekdays with no index data in the downloaded range are added to config/nse_holidays.yaml as
            "inferred" (review them with git diff; --no-write-holidays only prints them).
  options   expired NIFTY / BANKNIFTY option contracts near the money for every expiry in the range
            (scripts/fetch_expired_bulk, no daily cap). A weekly expiry that returns no data is probed on the
            other days of its week; findings go to data/fyers/expiry_map.json. After --stop-after-missing
            expiries in a row with no contract at all, that index is stopped: Fyers' intraday history ends there.
  stocks    NIFTY 50 daily candles from Fyers (re-fetches the whole range, so older Yahoo days are replaced
            where Fyers has them).
  archive   gzip CSV copy of all broker data with a checksummed manifest in --archive-dir (see
            scripts/archive_market_data for verify / restore).

Needs MARKET_DATA_PROVIDER=fyers and today's login (python -m trader.brokers.fyers.login). The Fyers token
expires at 06:00 IST: a run that crosses it stops after a few errors; sign in again and re-run.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from trader.brokers.fyers import expired, expired_bulk as bulk
from trader.config import CONFIG_DIR, get_settings
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.models import CandleRow, InstrumentRow
from trader.db.session import create_engine, create_sessionmaker
from trader.instruments import equities as eq
from trader.instruments import holiday_inference as hol
from trader.instruments.calendar import IST, get_calendar
from trader.market_data import archive, deep_backfill, equity_daily
from trader.market_data.factory import create_provider
from trader.market_data.service import MarketDataService

STEPS = ("index", "holidays", "options", "stocks", "archive")
CORE = ("NIFTY", "BANKNIFTY", "INDIAVIX", "NIFTY-FUT", "BANKNIFTY-FUT")


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def _earliest_future(sm, underlying: str) -> datetime | None:  # noqa: ANN001
    """First 1m candle of any futures contract of `underlying` (the loader merges them all)."""
    async with sm() as s:
        ts = (await s.execute(select(func.min(CandleRow.ts)).join(InstrumentRow, InstrumentRow.id == CandleRow.instrument_id)
                              .where(InstrumentRow.underlying == underlying, InstrumentRow.instrument_type == "FUT",
                                     InstrumentRow.token.not_like("SIM:%"), CandleRow.timeframe == "1m"))).scalar_one_or_none()
    return _utc(ts) if ts else None


async def step_index(settings, sm, start: datetime, now: datetime) -> None:  # noqa: ANN001
    provider = create_provider(settings)
    if provider.is_simulated:
        raise SystemExit("MARKET_DATA_PROVIDER is 'simulated'. Set it to 'fyers' in .env.")
    md = MarketDataService(provider, sm, settings)
    instruments = await provider.discover_instruments()
    md.registry.add_many(instruments)
    async with sm() as s, s.begin():
        md.token_to_id = await repo.upsert_instruments(s, instruments)
    for sym in CORE:
        inst = md.candle_instrument(sym)
        if inst is None:
            print(f"  {sym}: not in today's symbol master, skipped")
            continue
        iid = md.token_to_id[inst.token]
        if sym.endswith("-FUT"):
            # The near-month token changes every month and Fyers serves a continuous series under it: deep-fill
            # once. If an earlier contract's candles already reach back to the start, nothing older is needed.
            got = await _earliest_future(sm, inst.underlying)
            if got is not None and got <= start + timedelta(days=7):
                print(f"  {sym}: futures history already reaches {got:%Y-%m-%d}")
                deep = None
            else:
                deep = await deep_backfill.backfill_older(provider.history, sm, inst, iid, "1m", start, now)
        else:
            deep = await deep_backfill.backfill_older(provider.history, sm, inst, iid, "1m", start, now)
        fwd = await md.backfill_instrument(inst, "1m", now - timedelta(days=30), now)
        if deep is None:
            print(f"  {sym}: +{fwd} newer")
        elif deep.earliest is None:
            print(f"  {sym}: nothing returned ({deep.note})")
        else:
            where = "the requested start" if deep.reached_start else f"the start of Fyers' history ({deep.note})"
            print(f"  {sym}: +{deep.stored} older, +{fwd} newer; first candle {deep.earliest:%Y-%m-%d}, "
                  f"reached {where}")


async def step_holidays(sm, write: bool) -> list[date]:  # noqa: ANN001
    cal = get_calendar()
    days = await hol.stored_days(sm, ["NIFTY", "BANKNIFTY"])
    new = hol.missing_weekdays(days, set(cal.holidays))
    if not days:
        print("  no index candles stored yet: run the index step first")
        return []
    print(f"  {len(days)} trading days stored ({min(days)} .. {max(days)}); {len(new)} weekdays without data")
    for d in new:
        print(f"    {d} {d:%a}")
    if new and write:
        hol.append_holidays(CONFIG_DIR / "nse_holidays.yaml", new)
        print("  added to config/nse_holidays.yaml as 'inferred'; review with git diff and confirm against NSE circulars")
    return new


async def step_options(settings, sm, start: date, end: date, today: date, a: argparse.Namespace) -> None:  # noqa: ANN001
    from trader.brokers.fyers.provider import FyersMarketDataProvider

    fyers_dir = settings.data_dir / "fyers"
    ledger = bulk.EmptyLedger(fyers_dir / "expired_empty.json")
    emap = bulk.ExpiryMap(fyers_dir / "expiry_map.json")
    cal = get_calendar()  # re-read: the holidays step may have just added days
    rest = None if a.dry_run else FyersMarketDataProvider(settings).rest()
    for und in a.underlyings:
        plan = await bulk.build_plan(sm, cal, [und], start, end, timeframe="1m", pad=a.pad, today=today,
                                     ledger=ledger, expiry_map=emap)
        print(f"  {und}:")
        for line in bulk.summarise(plan, None):
            print(f"    {line}")
        if a.dry_run or not plan.items:
            continue
        res = await bulk.fetch(rest, sm, plan, timeframe="1m", quota=expired.DailyQuota(fyers_dir / "expired_quota.json", 0),
                               ledger=ledger, pause=a.pause, expiry_map=emap, cal=cal,
                               stop_after_missing_windows=a.stop_after_missing, say=(lambda _: None) if a.quiet else print)
        print(f"    {und}: {res.fetched} contracts ({res.candles} candles), {res.no_data} with no data, "
              f"{res.errors} errors." + (f" Stopped: {res.stopped}." if res.stopped else ""))


async def step_stocks(settings, sm, years: int) -> None:  # noqa: ANN001
    provider = create_provider(settings)
    out = await equity_daily.refresh(provider, sm, eq.expand(["NIFTY50"]), years, full=True)
    print(f"  {sum(out['stored'].values())} daily candles for {len(out['stored'])} stocks; {len(out['failed'])} failed")
    for sym, why in out["failed"].items():
        print(f"    {sym}: {why}")


async def run(a: argparse.Namespace) -> None:
    base = get_settings()
    days = a.years * 366 + 7
    # The provider clamps requests to the plan's depth; Prime serves years, so lift the clamp for this run.
    settings = base.model_copy(update={"fyers_intraday_history_days": max(days, base.fyers_intraday_history_days),
                                       "fyers_expired_intraday_daily_limit": 0})
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    now = datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    start_day = a.start or today - timedelta(days=a.years * 365)
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    print(f"Range {start_day} .. {today}; steps: {', '.join(a.steps)}" + (" (dry run)" if a.dry_run else ""))
    try:
        for step in a.steps:
            print(f"\n== {step} ==")
            if step == "index":
                if a.dry_run:
                    print("  would fetch 1m candles for " + ", ".join(CORE) + f" back to {start_day} (about "
                          f"{a.years * 4 * len(CORE)} requests of 90 days)")
                else:
                    await step_index(settings, sm, start, now)
            elif step == "holidays":
                await step_holidays(sm, write=a.write_holidays and not a.dry_run)
            elif step == "options":
                await step_options(settings, sm, start_day, today, today, a)
            elif step == "stocks":
                if a.dry_run:
                    print(f"  would fetch {len(eq.expand(['NIFTY50']))} stocks' daily candles for {a.years} years")
                else:
                    await step_stocks(settings, sm, a.years)
            elif step == "archive":
                root = a.archive_dir or settings.data_dir / "archive"
                if a.dry_run:
                    print(f"  would write the archive to {root}")
                else:
                    res = await archive.export(sm, root)
                    print(f"  {root}: {len(res.written)} files written ({res.rows} rows), {res.unchanged} unchanged")
    finally:
        await engine.dispose()
    if not a.dry_run and "options" in a.steps:
        print("\nNote: backtests take the lot size of old contracts from config/lot_sizes.yaml, not from the download. "
              "Its rows are marked unverified until checked against the NSE circulars; until then, rupee P&L for "
              "old years carries a warning.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--years", type=int, default=7)
    p.add_argument("--start", type=date.fromisoformat, help="overrides --years for the start date")
    p.add_argument("--steps", default=",".join(STEPS), help=f"comma list of {', '.join(STEPS)}")
    p.add_argument("--underlying", default="both", choices=["NIFTY", "BANKNIFTY", "both"])
    p.add_argument("--pad", type=int, default=1, help="extra strikes either side of the ones that could be ATM")
    p.add_argument("--pause", type=float, default=0.4, help="seconds between expired-option requests")
    p.add_argument("--stop-after-missing", type=int, default=3,
                   help="stop an index after this many expiries in a row with no contract at all")
    p.add_argument("--no-write-holidays", dest="write_holidays", action="store_false")
    p.add_argument("--archive-dir", type=Path)
    p.add_argument("--quiet", action="store_true", help="one line per index instead of one per contract")
    p.add_argument("--dry-run", action="store_true", help="plan only: no requests, nothing written")
    a = p.parse_args()
    a.steps = [s.strip() for s in a.steps.split(",") if s.strip()]
    bad = [s for s in a.steps if s not in STEPS]
    if bad:
        p.error(f"unknown step(s): {', '.join(bad)}")
    a.steps = [s for s in STEPS if s in a.steps]  # always in dependency order
    a.underlyings = ["NIFTY", "BANKNIFTY"] if a.underlying == "both" else [a.underlying]
    asyncio.run(run(a))


if __name__ == "__main__":
    main()
