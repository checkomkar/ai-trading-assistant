"""ATM and relative-strike resolvers (PRD §3.2, §3.3).

Offsets are plain strike steps: `ATM+1` is one interval above ATM regardless of CE/PE.
Use `moneyness_offset` to turn "N strikes ITM" into a step for a given option type.
"""

from __future__ import annotations

import math
from enum import Enum

from trader.instruments.specs import get_spec


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


def atm_strike(underlying: str, price: float, interval: int | None = None) -> int:
    step = interval or get_spec(underlying).strike_interval
    # Round half up: Python's round() is banker's rounding, which would make 25025 -> 25000.
    return int(math.floor(price / step + 0.5) * step)


def strike_at(underlying: str, price: float, offset: int, interval: int | None = None) -> int:
    step = interval or get_spec(underlying).strike_interval
    return atm_strike(underlying, price, step) + offset * step


def strike_ladder(underlying: str, price: float, width: int, interval: int | None = None) -> list[int]:
    step = interval or get_spec(underlying).strike_interval
    atm = atm_strike(underlying, price, step)
    return [atm + i * step for i in range(-width, width + 1)]


def moneyness_offset(option_type: OptionType, steps_itm: int) -> int:
    """Strike offset for `steps_itm` strikes in-the-money (negative = OTM)."""
    return -steps_itm if option_type == OptionType.CE else steps_itm
