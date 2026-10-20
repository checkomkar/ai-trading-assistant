"""Fallback contract specifications (PRD §14.2).

These are used by the simulator and when the broker instrument master is unavailable.
Real values must come from the broker instrument master: lot sizes and expiry rules
change through SEBI / NSE circulars.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExpiryCycle(str, Enum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


@dataclass(frozen=True)
class UnderlyingSpec:
    symbol: str
    strike_interval: int
    lot_size: int
    tick_size: float
    expiry_weekday: int  # Monday=0, Tuesday=1, ...
    expiry_cycle: ExpiryCycle


UNDERLYINGS: dict[str, UnderlyingSpec] = {
    "NIFTY": UnderlyingSpec("NIFTY", 50, 65, 0.05, expiry_weekday=1, expiry_cycle=ExpiryCycle.WEEKLY),
    "BANKNIFTY": UnderlyingSpec("BANKNIFTY", 100, 30, 0.05, expiry_weekday=1, expiry_cycle=ExpiryCycle.MONTHLY),
}

VIX_SYMBOL = "INDIAVIX"


def get_spec(symbol: str) -> UnderlyingSpec:
    try:
        return UNDERLYINGS[symbol.upper()]
    except KeyError:
        raise ValueError(f"Unsupported underlying: {symbol}") from None
