"""Expiry resolver (PRD §3.1)."""

from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from trader.instruments.calendar import IST, MARKET_CLOSE, TradingCalendar, get_calendar
from trader.instruments.specs import ExpiryCycle, UnderlyingSpec, get_spec


@dataclass(frozen=True)
class Expiries:
    nearest: date
    next: date
    monthly: date


def monthly_expiry(year: int, month: int, weekday: int, cal: TradingCalendar) -> date:
    """Last `weekday` of the month, rolled back over holidays."""
    last_day = date(year, month, _cal.monthrange(year, month)[1])
    d = last_day - timedelta(days=(last_day.weekday() - weekday) % 7)
    return cal.roll_back_to_trading_day(d)


def _weekly(start: date, weekday: int, cal: TradingCalendar, count: int) -> list[date]:
    d = start + timedelta(days=(weekday - start.weekday()) % 7)
    out: list[date] = []
    while len(out) < count:
        out.append(cal.roll_back_to_trading_day(d))
        d += timedelta(days=7)
    return out


def _monthly(start: date, weekday: int, cal: TradingCalendar, count: int) -> list[date]:
    y, m = start.year, start.month
    out: list[date] = []
    while len(out) < count:
        out.append(monthly_expiry(y, m, weekday, cal))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _is_live(expiry: date, now: datetime) -> bool:
    """An expiry is live until market close (15:30 IST) on expiry day."""
    local = now.astimezone(IST)
    return expiry > local.date() or (expiry == local.date() and local.time() < MARKET_CLOSE)


def upcoming_expiries(
    underlying: str | UnderlyingSpec,
    now: datetime,
    count: int = 4,
    cal: TradingCalendar | None = None,
) -> list[date]:
    """Live (not yet expired) expiries in ascending order."""
    spec = underlying if isinstance(underlying, UnderlyingSpec) else get_spec(underlying)
    cal = cal or get_calendar()
    # Start a week early so a holiday roll-back onto/after today is not skipped.
    start = now.astimezone(IST).date() - timedelta(days=7)
    gen = _weekly if spec.expiry_cycle == ExpiryCycle.WEEKLY else _monthly
    candidates = gen(start, spec.expiry_weekday, cal, count + 3)
    return sorted({d for d in candidates if _is_live(d, now)})[:count]


def resolve_expiries(underlying: str, now: datetime, cal: TradingCalendar | None = None) -> Expiries:
    spec = get_spec(underlying)
    cal = cal or get_calendar()
    nearest, nxt = upcoming_expiries(spec, now, count=2, cal=cal)
    monthly = next(d for d in _monthly(now.astimezone(IST).date(), spec.expiry_weekday, cal, 2) if _is_live(d, now))
    return Expiries(nearest=nearest, next=nxt, monthly=monthly)
