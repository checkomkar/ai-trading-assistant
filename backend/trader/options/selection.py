"""Which strike a signal buys (PRD §24.8 B7.1). One function for the backtester and the paper runner, so both pick the
same contract for the same spot.

Selections:
* ``ATM``: the strike nearest the spot (half rounds up, as `instruments.strikes.atm_strike`).
* ``ITMn`` / ``OTMn``: n strikes in / out of the money from ATM (CE in the money = lower strikes; PE = higher).
* ``Dnn``: the strike whose Black-Scholes delta is closest to 0.nn in absolute value (``D40`` = delta 0.40). The
  volatility is INDIA VIX x the underlying's multiplier, the same rule synthetic pricing uses, so the choice is
  reproducible from the index and VIX alone; it is not the option's own implied volatility.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from trader.instruments.specs import get_spec
from trader.instruments.strikes import OptionType, atm_strike, moneyness_offset
from trader.options import black_scholes as bs

IV_MULT = {"NIFTY": 1.0, "BANKNIFTY": 1.15}
DEFAULT_IV = 0.14
SEARCH_STEPS = 12  # strikes either side of ATM considered for a delta target
_SEL = re.compile(r"^(ATM|ITM[1-5]|OTM[1-5]|D[1-9][0-9]?)$")


def valid(selection: str) -> bool:
    return bool(_SEL.match(selection.upper()))


def iv_from_vix(underlying: str, vix: float | None) -> float:
    return DEFAULT_IV if vix is None or vix <= 0 else vix / 100 * IV_MULT.get(underlying.upper(), 1.0)


def offset_steps(option_type: OptionType, selection: str) -> int:
    """Strike steps from ATM for ITMn / OTMn (0 for ATM)."""
    sel = selection.upper()
    if sel.startswith(("ITM", "OTM")):
        n = int(sel[3:] or 1)
        return moneyness_offset(option_type, n if sel.startswith("ITM") else -n)
    return 0


def choose_strike(underlying: str, option_type: OptionType, spot: float, selection: str = "ATM", *,
                  expiry: date | None = None, at: datetime | None = None, vix: float | None = None,
                  r: float = 0.065, available: list[float] | None = None) -> float | None:
    """The strike to buy. `available` (the broker's listed strikes) limits the answer to strikes that exist; None for an
    exact grid (backtests). None when the selection is invalid or a delta target lacks expiry/time."""
    sel = selection.upper()
    if not valid(sel):
        return None
    step = get_spec(underlying).strike_interval
    atm = float(atm_strike(underlying, spot, step))
    if sel.startswith("D"):
        if expiry is None or at is None:
            return None
        target = int(sel[1:]) / 100
        iv = iv_from_vix(underlying, vix)
        t = max(bs.time_to_expiry(expiry, at), bs.MIN_T)
        cands = [atm + i * step for i in range(-SEARCH_STEPS, SEARCH_STEPS + 1)]
        if available is not None:
            have = set(available)
            cands = [k for k in cands if k in have]
        if not cands:
            return None
        return min(cands, key=lambda k: (abs(abs(bs.greeks(spot, k, t, r, iv, option_type).delta) - target), k))
    k = atm + offset_steps(option_type, sel) * step
    if available is not None and k not in set(available):
        return None
    return k
