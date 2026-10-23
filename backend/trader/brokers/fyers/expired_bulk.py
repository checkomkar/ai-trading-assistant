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


class ExpiryMap:
    """Expiries found by probing (PRD §24.2 B1.4): "NIFTY|2021-03-25" -> the date Fyers actually listed, or None
    when no nearby day had the contract either. Pre-2025 expiry rules and inferred holidays are best-effort;
    this records what the exchange really did, so a re-run plans the right symbols and later phases can check
    the expiry rules against it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            raw = {}
        self._m: dict[str, dict | None] = raw

    @staticmethod
    def key(underlying: str, planned: date) -> str:
        return f"{underlying}|{planned.isoformat()}"

    def __contains__(self, key: str) -> bool:
        return key in self._m

    def get(self, underlying: str, planned: date) -> tuple[date, bool] | None:
        v = self._m.get(self.key(underlying, planned))
        return (date.fromisoformat(v["expiry"]), bool(v["monthly"])) if v else None

    def set(self, underlying: str, planned: date, found: tuple[date, bool] | None) -> None:
        self._m[self.key(underlying, planned)] = (
            {"expiry": found[0].isoformat(), "monthly": found[1]} if found else None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._m, indent=1, sort_keys=True), encoding="utf-8")

    def items(self) -> dict[str, dict | None]:
        return dict(self._m)


def alternates(w: Window, cal: TradingCalendar) -> list[tuple[date, bool]]:
    """Other (expiry, monthly-format) pairs a weekly contract may really have had: the other trading days of
    the same week, nearest first, and the monthly symbol when that day falls in the month's last week."""
    monday = w.expiry - timedelta(days=w.expiry.weekday())
    days = sorted((monday + timedelta(days=i) for i in range(5)), key=lambda d: (abs((d - w.expiry).days), d))
    out: list[tuple[date, bool]] = []
    for d in days:
        if not cal.is_trading_day(d):
            continue
        last_week = (d + timedelta(days=7)).month != d.month
        if d != w.expiry or w.monthly:
            out.append((d, False))
        if last_week and not (d == w.expiry and w.monthly):
            out.append((d, True))
    return out


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
                     newest_first: bool = True, expiry_map: ExpiryMap | None = None) -> Plan:
    """Everything still to download, newest expiry first (the most useful if a quota cuts the run short)."""
    items: list[Item] = []
    have_tokens = await stored_tokens(sm, timeframe)
    stored = empty = 0
    skipped: list[str] = []
    for und in underlyings:
        wins = expiry_windows(und, start, end, cal, today)
        for w in (reversed(wins) if newest_first else wins):
            if expiry_map is not None and ExpiryMap.key(und, w.expiry) in expiry_map:
                found = expiry_map.get(und, w.expiry)
                if found is None:
                    skipped.append(f"{und} {w.expiry} (no contract found on any day that week)")
                    continue
                w = Window(und, found[0], w.start, found[1])
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
                sleep: Callable[[float], object] = asyncio.sleep,
                expiry_map: ExpiryMap | None = None, cal: TradingCalendar | None = None,
                probe_after: int = 4, stop_after_missing_windows: int | None = None) -> Result:
    """Download `plan.items` in order. Safe to interrupt and re-run: stored contracts are skipped.

    With `expiry_map` and `cal`, a weekly expiry whose first `probe_after` contracts all come back empty is
    taken as a wrong date (old expiry rules, an unknown holiday): the other days of that week are probed with
    one near-the-money contract, and the rest of the window is fetched under the date that has data.
    `stop_after_missing_windows` ends the run after that many expiries in a row with no contract on any day:
    walking newest first, that is where the broker's intraday history stops."""
    res = Result()
    intraday = timeframe != "1d"
    streak = 0
    done: set[str] = set()  # symbols already asked for in this run (a probe hit is not fetched twice)

    def budget_left() -> bool:
        if max_requests is not None and res.fetched + res.no_data + res.errors >= max_requests:
            res.stopped = f"reached --max-requests {max_requests}"
            return False
        if intraday and quota.remaining() == 0:
            res.stopped = f"daily quota of {quota.limit} used (re-run tomorrow, or use --prime)"
            return False
        return True

    async def get(it: Item) -> int | None:
        """Candles stored for `it`, 0 for no data, None for an error (counted; may stop the run)."""
        nonlocal streak
        inst = expired.expired_instrument(it.symbol, it.window.expiry)
        done.add(it.symbol)
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
            else:
                await sleep(min(60.0, 2.0 ** streak))
            return None
        streak = 0
        if not candles:
            ledger.add(f"{inst.token}|{timeframe}")
            res.no_data += 1
            say(f"  {inst.symbol}: no data")
            await sleep(pause)
            return 0
        async with sm() as s, s.begin():
            ids = await repo.upsert_instruments(s, [inst], is_active=False)
            n = await repo.upsert_candles(s, candles, ids)
            await repo.record_event(s, "fyers", "expired.downloaded",
                                    {"symbol": inst.token, "timeframe": timeframe, "candles": n})
        res.fetched += 1
        res.candles += n
        say(f"  {inst.symbol}: {n} candles")
        await sleep(pause)
        return n

    async def probe(w: Window, group: list[Item]) -> Window | None:
        mid = group[len(group) // 2]
        for exp, monthly in alternates(w, cal):  # type: ignore[arg-type]
            if not budget_left() or res.stopped:
                return None
            alt = Window(w.underlying, exp, w.start, monthly)
            got = await get(Item(alt, mid.strike, mid.option_type))
            if got:
                say(f"  {w.underlying} expiry {w.expiry} is really {exp}" + (" (monthly symbol)" if monthly else ""))
                return alt
        return None

    missing_streak = 0
    groups: list[list[Item]] = []
    for it in plan.items:
        if groups and groups[-1][0].window == it.window:
            groups[-1].append(it)
        else:
            groups.append([it])

    for group in groups:
        w = group[0].window
        can_probe = (expiry_map is not None and cal is not None and not w.monthly
                     and ExpiryMap.key(w.underlying, w.expiry) not in expiry_map)
        empties, got_any = 0, False
        i = 0
        while i < len(group):
            if group[i].symbol in done:
                i += 1
                continue
            if res.stopped or not budget_left():
                return res
            n = await get(group[i])
            if res.stopped:
                return res
            got_any = got_any or bool(n)
            empties = empties + 1 if n == 0 else empties
            i += 1
            if can_probe and not got_any and empties >= min(probe_after, len(group)):
                alt = await probe(w, group)
                if res.stopped:
                    return res
                expiry_map.set(w.underlying, w.expiry, (alt.expiry, alt.monthly) if alt else None)  # type: ignore[union-attr]
                if alt is None:
                    say(f"  {w.underlying} {w.expiry}: no contract on any day of that week; skipped")
                    missing_streak += 1
                    if stop_after_missing_windows and missing_streak >= stop_after_missing_windows:
                        res.stopped = (f"{missing_streak} expiries in a row with no data, the last {w.expiry}: "
                                       "Fyers' intraday history for this index appears to end there")
                        return res
                    break
                group = [Item(alt, g.strike, g.option_type) for g in group]
                i, can_probe, got_any = 0, False, True  # the probe itself found data
                continue
        if got_any:
            missing_streak = 0
        if can_probe and got_any:
            expiry_map.set(w.underlying, w.expiry, (w.expiry, w.monthly))  # type: ignore[union-attr]
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
