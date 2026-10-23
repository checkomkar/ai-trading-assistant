"""Cash-equity universe for daily-bar strategies: the NIFTY 50 stocks, with Fyers tickers.

The list is the index's membership as of this release (the index is reviewed twice a year, so it drifts). A symbol
Fyers does not know is reported and skipped by the backfill, never guessed. Daily candles are stored with timeframe
"1d", stamped at 09:15 IST of the trading day (UTC in the DB like every other candle), so day boundaries match the
intraday data.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from trader.domain import Instrument, InstrumentType
from trader.instruments.calendar import IST

DAILY = "1d"

NIFTY50: tuple[str, ...] = (
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
    "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK",
    "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK",
    "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
    "TMPV",
)
UNIVERSES: dict[str, tuple[str, ...]] = {"NIFTY50": NIFTY50}


def token(symbol: str) -> str:
    return f"NSE:{symbol}-EQ"


def instrument(symbol: str) -> Instrument:
    return Instrument(token(symbol), symbol, symbol, "NSE", InstrumentType.EQUITY, tick_size=0.05)


def is_equity_symbol(symbol: str) -> bool:
    return symbol in NIFTY50


def expand(names: list[str]) -> list[str]:
    """Universe names (`NIFTY50`) and/or symbols -> a de-duplicated list of symbols, order kept."""
    out: list[str] = []
    for n in names:
        for s in UNIVERSES.get(n, (n,)):
            if s not in out:
                out.append(s)
    return out


def day_stamp(d: date) -> datetime:
    """The stored timestamp of a daily candle: 09:15 IST of the trading day, in UTC."""
    return datetime.combine(d, time(9, 15), tzinfo=IST).astimezone(timezone.utc)


def normalise_daily(ts: datetime) -> datetime:
    """Whatever instant the broker used for the day (midnight or 05:30 IST) -> the canonical stamp of that IST date."""
    return day_stamp(ts.astimezone(IST).date())


def day_of(ts: datetime) -> date:
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(IST).date()


WARMUP_DAYS = 420  # calendar days of history loaded before a run starts, so slow indicators (EMA 200) are ready


def warmup_start(start: date) -> date:
    return start - timedelta(days=WARMUP_DAYS)
