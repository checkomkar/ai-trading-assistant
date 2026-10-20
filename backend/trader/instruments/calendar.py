"""NSE trading calendar and market session state (PRD §1.7, §14.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from trader.config import CONFIG_DIR

IST = ZoneInfo("Asia/Kolkata")

PRE_OPEN_START = time(9, 0)
MARKET_OPEN = time(9, 15)
OPENING_PHASE_END = time(9, 30)
MARKET_CLOSE = time(15, 30)
CLOSING_SESSION_END = time(15, 40)


class SessionState(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"  # first 15 minutes of continuous trading
    TRADING = "TRADING"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


@dataclass
class TradingCalendar:
    holidays: dict[date, str] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> "TradingCalendar":
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        holidays = {_as_date(k): str(v) for k, v in (raw.get("holidays") or {}).items()}
        return cls(holidays=holidays)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def previous_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def roll_back_to_trading_day(self, d: date) -> date:
        """An expiry falling on a holiday moves to the previous trading day."""
        return d if self.is_trading_day(d) else self.previous_trading_day(d)

    def session_state(self, now: datetime) -> SessionState:
        local = now.astimezone(IST)
        if not self.is_trading_day(local.date()):
            return SessionState.CLOSED
        t = local.time()
        if PRE_OPEN_START <= t < MARKET_OPEN:
            return SessionState.PRE_OPEN
        if MARKET_OPEN <= t < OPENING_PHASE_END:
            return SessionState.OPEN
        if OPENING_PHASE_END <= t < MARKET_CLOSE:
            return SessionState.TRADING
        if MARKET_CLOSE <= t < CLOSING_SESSION_END:
            return SessionState.CLOSING
        return SessionState.CLOSED

    def is_market_open(self, now: datetime) -> bool:
        return self.session_state(now) in (SessionState.OPEN, SessionState.TRADING)


def _as_date(v: object) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


@lru_cache
def get_calendar() -> TradingCalendar:
    return TradingCalendar.from_yaml(CONFIG_DIR / "nse_holidays.yaml")
