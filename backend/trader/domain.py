"""Shared domain types used across services (the PRD's `packages/models`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from trader.instruments.strikes import OptionType


class InstrumentType(str, Enum):
    INDEX = "INDEX"
    FUT = "FUT"
    OPT = "OPT"


@dataclass(frozen=True)
class Instrument:
    token: str
    symbol: str  # exchange trading symbol, e.g. NIFTY28OCT2525000CE
    underlying: str
    exchange: str  # NSE (index) / NFO (derivatives)
    instrument_type: InstrumentType
    lot_size: int = 1
    tick_size: float = 0.05
    expiry: date | None = None
    strike: float | None = None
    option_type: OptionType | None = None

    @property
    def is_option(self) -> bool:
        return self.instrument_type == InstrumentType.OPT


@dataclass
class Tick:
    token: str
    ts: datetime  # timezone-aware UTC
    ltp: float
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None  # cumulative day volume
    oi: int | None = None
    open: float | None = None  # day OHLC
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    prev_oi: int | None = None

    @property
    def oi_change(self) -> int | None:
        if self.oi is None or self.prev_oi is None:
            return None
        return self.oi - self.prev_oi
