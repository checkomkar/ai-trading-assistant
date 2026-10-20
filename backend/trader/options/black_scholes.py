"""Black-Scholes pricing, Greeks and implied volatility (PRD §6.1-6.3, §14.6).

NSE index options are European-style and cash-settled, so plain Black-Scholes on the
index (dividend yield q, default 0) is an adequate first model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from trader.instruments.strikes import OptionType

IST = ZoneInfo("Asia/Kolkata")
EXPIRY_TIME = time(15, 30)
SECONDS_PER_YEAR = 365.0 * 24 * 3600
MIN_T = 1.0 / SECONDS_PER_YEAR * 60  # floor at one minute to avoid division by zero at expiry


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def time_to_expiry(expiry: date, now: datetime) -> float:
    """Calendar time in years until 15:30 IST on expiry day."""
    expiry_dt = datetime.combine(expiry, EXPIRY_TIME, tzinfo=IST)
    return max((expiry_dt - now).total_seconds() / SECONDS_PER_YEAR, 0.0)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 vol point (1%)


def _d1_d2(s: float, k: float, t: float, r: float, sigma: float, q: float) -> tuple[float, float]:
    vs = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / vs
    return d1, d1 - vs


def price(s: float, k: float, t: float, r: float, sigma: float, opt: OptionType, q: float = 0.0) -> float:
    if t <= 0 or sigma <= 0:
        return intrinsic(s, k, opt)
    d1, d2 = _d1_d2(s, k, t, r, sigma, q)
    if opt == OptionType.CE:
        return s * math.exp(-q * t) * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * math.exp(-q * t) * _ncdf(-d1)


def greeks(s: float, k: float, t: float, r: float, sigma: float, opt: OptionType, q: float = 0.0) -> Greeks:
    t = max(t, MIN_T)
    d1, d2 = _d1_d2(s, k, t, r, sigma, q)
    sqrt_t = math.sqrt(t)
    disc_q, disc_r = math.exp(-q * t), math.exp(-r * t)
    gamma = disc_q * _npdf(d1) / (s * sigma * sqrt_t)
    vega = s * disc_q * _npdf(d1) * sqrt_t / 100.0
    common = -s * disc_q * _npdf(d1) * sigma / (2 * sqrt_t)
    if opt == OptionType.CE:
        delta = disc_q * _ncdf(d1)
        theta = common - r * k * disc_r * _ncdf(d2) + q * s * disc_q * _ncdf(d1)
    else:
        delta = disc_q * (_ncdf(d1) - 1)
        theta = common + r * k * disc_r * _ncdf(-d2) - q * s * disc_q * _ncdf(-d1)
    return Greeks(delta=delta, gamma=gamma, theta=theta / 365.0, vega=vega)


def intrinsic(s: float, k: float, opt: OptionType) -> float:
    return max(s - k, 0.0) if opt == OptionType.CE else max(k - s, 0.0)


def implied_vol(
    market_price: float,
    s: float,
    k: float,
    t: float,
    r: float,
    opt: OptionType,
    q: float = 0.0,
    tol: float = 1e-5,
    max_iter: int = 100,
) -> float | None:
    """Newton-Raphson with bisection fallback. Returns None when no meaningful IV exists."""
    if t <= 0 or market_price <= 0 or s <= 0 or k <= 0:
        return None
    lower_bound = max(intrinsic(s * math.exp(-q * t), k * math.exp(-r * t), opt), 0.0)
    if market_price < lower_bound - 1e-6:
        return None

    lo, hi = 1e-4, 5.0
    if market_price > price(s, k, t, r, hi, opt, q):
        return None

    sigma = 0.2
    for _ in range(max_iter):
        p = price(s, k, t, r, sigma, opt, q)
        diff = p - market_price
        if abs(diff) < tol:
            return sigma
        if diff > 0:
            hi = sigma
        else:
            lo = sigma
        vega = s * math.exp(-q * t) * _npdf(_d1_d2(s, k, t, r, sigma, q)[0]) * math.sqrt(t)
        step_ok = vega > 1e-8
        if step_ok:
            candidate = sigma - diff / vega
            step_ok = lo < candidate < hi
        sigma = candidate if step_ok else 0.5 * (lo + hi)
    return sigma if abs(price(s, k, t, r, sigma, opt, q) - market_price) < 1e-2 else None


def moneyness(s: float, k: float, opt: OptionType, atm_band: float = 0.0) -> str:
    """ITM / ATM / OTM. `atm_band` is the absolute distance treated as ATM (e.g. half a strike step)."""
    if abs(s - k) <= atm_band:
        return "ATM"
    itm = s > k if opt == OptionType.CE else s < k
    return "ITM" if itm else "OTM"
