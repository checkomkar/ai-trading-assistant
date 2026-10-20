"""Bulk download of expired option candles for a date range (PRD §20.1).

`scripts/fetch_expired_options.py` takes one expiry and an ATM level typed by hand. That is fine for a
few contracts but not for a year of weekly expiries. This module plans the whole download:

* **Which expiries:** every expiry in the range under the exchange rules in force then
  (`backtest.pricing.expiry_for`), already expired, with its symbol format (weekly or monthly).
* **Which days per contract:** only the window in which a backtest could use it, from the previous
  expiry to its own, so each request is a few days, not 100.
* **Which strikes:** the at-the-money strikes the index actually visited in that window (from the stored
  index candles), plus `pad` strikes either side. A strike nobody could have traded at the money is
  never requested.

Nothing here talks to Fyers: `fetch` does, through an injected REST object, so it is tested with a fake.
Candles are stored at the finest timeframe (1m) because the backtest loader builds coarser ones exactly.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import select

from trader.backtest import data as bt_data
from trader.backtest.pricing import VERIFIED_RULES_FROM, _rule, expiry_for
from trader.brokers.fyers import expired
from trader.db import repository as repo
from trader.db.models import CandleRow, InstrumentRow
from trader.instruments.calendar import TradingCalendar
from trader.instruments.specs import ExpiryCycle, get_spec
from trader.instruments.strikes import OptionType, atm_strike


@dataclass(frozen=True)
class Window:
    underlying: str
    expiry: date
    start: date  # previous expiry: the first day this contract can be the nearest one
    monthly: bool  # monthly symbol format (NIFTY25NOV25000CE) rather than weekly (NIFTY25N1125000CE)


@dataclass(frozen=True)
class Item:
    window: Window
    strike: int
    option_type: OptionType

    @property
    def symbol(self) -> str:
        w = self.window
        return expired.build_symbol(w.underlying, w.expiry, self.strike, self.option_type, w.monthly)


def expiry_windows(underlying: str, start: date, end: date, cal: TradingCalendar, today: date) -> list[Window]:
    """Expired expiries whose usable window overlaps [start, end], oldest first."""
    seen: set[date] = set()
    d = start - timedelta(days=45)
    while d <= end + timedelta(days=10):
        seen.add(expiry_for(underlying, d, cal, True))
        d += timedelta(days=1)
    exps = sorted(seen)
    out: list[Window] = []
    for i in range(1, len(exps)):
        e, prev = exps[i], exps[i - 1]
        if e < start or prev > end or e >= today:  # not yet expired (or expiring today) means no history yet
            continue
        if _rule(underlying, e)[0] == ExpiryCycle.MONTHLY:
            monthly = True
        else:  # the last weekly expiry of a month is the monthly contract
            nxt = exps[i + 1] if i + 1 < len(exps) else expiry_for(underlying, e, cal, True)
            monthly = nxt.month != e.month
        out.append(Window(underlying, e, prev, monthly))
    return out


def strikes_for(underlying: str, lo: float, hi: float, pad: int) -> list[int]:
    """ATM strikes for every spot in [lo, hi], widened by `pad` strikes each side."""
    step = get_spec(underlying).strike_interval
    return list(range(atm_strike(underlying, lo) - pad * step, atm_strike(underlying, hi) + pad * step + 1, step))


async def spot_range(sm, underlying: str, start: date, end: date) -> tuple[float, float] | None:  # noqa: ANN001
    """Lowest and highest bar-open of the stored real index candles in the window (the price the backtest
    uses to choose ATM at entry). None if the index was not stored for these days."""
    try:
        async with sm() as s:
            data = await bt_data.load(s, underlying, "5m", start, end, "real")
    except LookupError:
        return None
    opens = [b.open for b in data.bars]
    return (min(opens), max(opens)) if opens else None


async def stored_tokens(sm, timeframe: str) -> set[str]:  # noqa: ANN001
    """Option contracts that already have candles at `timeframe`."""
    q = (select(InstrumentRow.token).join(CandleRow, CandleRow.instrument_id == InstrumentRow.id)
         .where(InstrumentRow.instrument_type == "OPT", CandleRow.timeframe == timeframe).distinct())
    async with sm() as s:
        return set((await s.execute(q)).scalars().all())


class EmptyLedger:
    """Contracts Fyers answered "no data" for (a deep strike that never traded). Remembered so a resumed
    run does not spend its daily quota on them again."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self._tokens = set(json.loads(path.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError):
            self._tokens = set()

    def __contains__(self, key: str) -> bool:
        return key in self._tokens

    def add(self, key: str) -> None:
        self._tokens.add(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self._tokens)), encoding="utf-8")


@dataclass
class Plan:
    items: list[Item]
    stored: int  # already in the database
    empty: int  # known to have no data
    skipped_windows: list[str]  # expiries with no stored index candles, so the strikes are unknown

    @property
    def to_fetch(self) -> list[Item]:
        return self.items


async def build_plan(sm, cal: TradingCalendar, underlyings: list[str], start: date, end: date, *,  # noqa: ANN001
                     timeframe: str, pad: int, today: date, ledger: EmptyLedger,
                     newest_first: bool = True) -> Plan:
    """Everything still to download, newest expiry first (the most useful if a quota cuts the run short)."""
    items: list[Item] = []
    have_tokens = await stored_tokens(sm, timeframe)
    stored = empty = 0
    skipped: list[str] = []
    for und in underlyings:
        wins = expiry_windows(und, start, end, cal, today)
        for w in (reversed(wins) if newest_first else wins):
            rng = await spot_range(sm, und, max(w.start, start), min(w.expiry, end))
            if rng is None:
                skipped.append(f"{und} {w.expiry}")
                continue
            for k in strikes_for(und, rng[0], rng[1], pad):
                for ot in OptionType:
                    it = Item(w, k, ot)
                    token = f"NSE:{it.symbol.removeprefix('NSE:')}"
                    if token in have_tokens:
                        stored += 1
                    elif f"{token}|{timeframe}" in ledger:
                        empty += 1
                    else:
                        items.append(it)
    return Plan(items, stored, empty, skipped)


@dataclass
class Result:
    fetched: int = 0
    candles: int = 0
    no_data: int = 0
    errors: int = 0
    stopped: str | None = None  # why the run ended early


async def fetch(rest, sm, plan: Plan, *, timeframe: str, quota: expired.DailyQuota,  # noqa: ANN001
                ledger: EmptyLedger, max_requests: int | None = None, pause: float = 0.4,
                max_consecutive_errors: int = 5, say: Callable[[str], None] = print,
                sleep: Callable[[float], object] = asyncio.sleep) -> Result:
    """Download `plan.items` in order. Safe to interrupt and re-run: stored contracts are skipped."""
    res = Result()
    intraday = timeframe != "1d"
    streak = 0
    for it in plan.items:
        if max_requests is not None and res.fetched + res.no_data + res.errors >= max_requests:
            res.stopped = f"reached --max-requests {max_requests}"
            break
        if intraday and quota.remaining() == 0:
            res.stopped = f"daily quota of {quota.limit} used (re-run tomorrow, or use --prime)"
            break
        inst = expired.expired_instrument(it.symbol, it.window.expiry)
        try:
            resp = await asyncio.to_thread(rest.fno_historical_data, expired.request(
                inst.token, timeframe, it.window.start, it.window.expiry))
            if intraday:
                quota.consume()
            candles = expired.parse_response(resp, inst.token, timeframe)
        except Exception as exc:  # noqa: BLE001 - a network blip, a rate limit or an expired token
            res.errors += 1
            streak += 1
            say(f"  {inst.symbol}: {type(exc).__name__}: {exc}")
            if streak >= max_consecutive_errors:
                res.stopped = (f"{streak} errors in a row (rate limited, token expired, or Fyers is down); "
                               "stopped before using more requests")
                break
            await sleep(min(60.0, 2.0 ** streak))
            continue
        streak = 0
        if not candles:
            ledger.add(f"{inst.token}|{timeframe}")
            res.no_data += 1
            say(f"  {inst.symbol}: no data")
        else:
            async with sm() as s, s.begin():
                ids = await repo.upsert_instruments(s, [inst], is_active=False)
                n = await repo.upsert_candles(s, candles, ids)
                await repo.record_event(s, "fyers", "expired.downloaded",
                                        {"symbol": inst.token, "timeframe": timeframe, "candles": n})
            res.fetched += 1
            res.candles += n
            say(f"  {inst.symbol}: {n} candles")
        await sleep(pause)
    return res


def summarise(plan: Plan, per_day: int | None) -> list[str]:
    n = len(plan.items)
    lines = [f"{n} contracts to download, {plan.stored} already stored, {plan.empty} known empty."]
    if per_day:
        days = -(-n // per_day)
        lines.append(f"At {per_day} requests a day that is {days} day(s). With --prime there is no daily cap.")
    if plan.skipped_windows:
        lines.append(f"{len(plan.skipped_windows)} expiries skipped: no stored index candles for those days "
                     f"(backfill history first), e.g. {', '.join(plan.skipped_windows[:3])}.")
    if any(it.window.expiry < VERIFIED_RULES_FROM for it in plan.items):
        lines.append(f"Expiries before {VERIFIED_RULES_FROM} use best-effort historical expiry rules; "
                     "contracts that do not exist will come back as 'no data'.")
    return lines
