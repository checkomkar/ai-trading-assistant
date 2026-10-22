"""Option premiums for backtests (PRD §14.5, §15).

synthetic — Black-Scholes on the index, IV = INDIA VIX x multiplier, modelled spread.
            Works on index history alone; optimistic about liquidity, so reports say so.
real      — stored option candles (Fyers expired-contract downloads or our own recordings).
            Contracts without data are skipped and counted, never filled at made-up prices.
hybrid    — real where the contract has a stored print at the entry bar, otherwise the synthetic model for that whole trade.
            Every trade is tagged with the source it used, and the report counts both, so nothing is hidden. Needed because
            stored option candles are sparse (the free Fyers plan allows 10 expired-contract downloads a day).
"""

from __future__ import annotations

import calendar as _cal
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from trader.instruments.calendar import TradingCalendar
from trader.instruments.specs import ExpiryCycle, get_spec
from trader.instruments.strikes import OptionType, atm_strike
from trader.market_data.candles import Candle
from trader.options import black_scholes as bs


# NSE expiry conventions over time: (effective_from, cycle, weekday Mon=0). Used to pick the
# contract a strategy would have traded on a historical date. Pre-2025 rows: verify before
# relying on synthetic results for those years.
EXPIRY_RULES: dict[str, list[tuple[date, ExpiryCycle, int]]] = {
    "NIFTY": [(date(2000, 1, 1), ExpiryCycle.WEEKLY, 3), (date(2025, 9, 1), ExpiryCycle.WEEKLY, 1)],
    "BANKNIFTY": [(date(2000, 1, 1), ExpiryCycle.WEEKLY, 3), (date(2023, 9, 4), ExpiryCycle.WEEKLY, 2),
                  (date(2024, 11, 20), ExpiryCycle.MONTHLY, 3), (date(2025, 9, 1), ExpiryCycle.MONTHLY, 1)],
}
VERIFIED_RULES_FROM = date(2025, 9, 1)


def _rule(underlying: str, d: date) -> tuple[ExpiryCycle, int]:
    rules = [r for r in EXPIRY_RULES[underlying] if r[0] <= d]
    _, cycle, wd = rules[-1]
    return cycle, wd


def _last_weekday(y: int, m: int, wd: int) -> date:
    last = date(y, m, _cal.monthrange(y, m)[1])
    return last - timedelta(days=(last.weekday() - wd) % 7)


def expiry_for(underlying: str, day: date, cal: TradingCalendar, roll_on_expiry_day: bool = True) -> date:
    """Nearest expiry on/after `day` under the rules in force then (holidays roll back a day)."""
    cycle, wd = _rule(underlying, day)
    candidates: list[date] = []
    if cycle == ExpiryCycle.WEEKLY:
        d = day + timedelta(days=(wd - day.weekday()) % 7) - timedelta(days=7)
        for _ in range(4):
            candidates.append(cal.roll_back_to_trading_day(d))
            d += timedelta(days=7)
    else:
        y, m = day.year, day.month
        for _ in range(3):
            candidates.append(cal.roll_back_to_trading_day(_last_weekday(y, m, wd)))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    live = sorted(c for c in candidates if c > day or (c == day and not roll_on_expiry_day))
    return live[0]


@dataclass(frozen=True)
class Contract:
    underlying: str
    expiry: date
    strike: float
    option_type: OptionType
    lot_size: int
    synthetic: bool = False  # priced by the model (hybrid mode) instead of stored candles

    @property
    def label(self) -> str:
        return f"{self.underlying} {self.expiry:%d%b%y} {self.strike:g} {self.option_type.value}".upper()


@dataclass(frozen=True)
class PremiumBar:
    """Mid-price premium path over one underlying bar."""
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


@dataclass(frozen=True)
class ExecutionModel:
    spread_pct: float = 0.30  # full bid-ask spread as % of premium (ATM index options are tight)
    slippage_ticks: int = 1
    tick: float = 0.05

    def half_spread(self, mid: float) -> float:
        return max(self.tick / 2, mid * self.spread_pct / 200)

    def buy(self, mid: float) -> float:
        return _round_tick(mid + self.half_spread(mid) + self.slippage_ticks * self.tick, self.tick)

    def sell(self, mid: float) -> float:
        return max(self.tick, _round_tick(mid - self.half_spread(mid) - self.slippage_ticks * self.tick, self.tick))


def _round_tick(x: float, tick: float) -> float:
    return round(round(x / tick) * tick, 2)


class SyntheticPricer:
    mode = "synthetic"

    def __init__(self, cal: TradingCalendar, risk_free_rate: float = 0.065, iv_multiplier: dict | None = None,
                 default_iv: float = 0.14, roll_on_expiry_day: bool = True) -> None:
        self.cal, self.r = cal, risk_free_rate
        self.iv_mult = iv_multiplier or {"NIFTY": 1.0, "BANKNIFTY": 1.15}
        self.default_iv = default_iv
        self.roll = roll_on_expiry_day
        self.missing_vix_bars = 0
        self.unverified_expiry_dates: set[date] = set()

    def contract(self, underlying: str, option_type: OptionType, spot: float, day: date,
                 lot_size: int | None = None) -> Contract | None:
        if day < VERIFIED_RULES_FROM:
            self.unverified_expiry_dates.add(day)
        spec = get_spec(underlying)
        return Contract(underlying, expiry_for(underlying, day, self.cal, self.roll),
                        float(atm_strike(underlying, spot)), option_type, lot_size or spec.lot_size)

    def iv(self, underlying: str, vix: float | None) -> float:
        if vix is None or vix <= 0:
            self.missing_vix_bars += 1
            return self.default_iv
        return vix / 100 * self.iv_mult.get(underlying, 1.0)

    def premium(self, c: Contract, spot: float, at: datetime, iv: float) -> float:
        t = max(bs.time_to_expiry(c.expiry, at), bs.MIN_T)
        return max(bs.price(spot, c.strike, t, self.r, iv, c.option_type), 0.05)

    def bar(self, c: Contract, u: Candle, tf: timedelta, vix: float | None) -> PremiumBar | None:
        iv = self.iv(c.underlying, vix)
        mid_t = u.ts + tf / 2
        o = self.premium(c, u.open, u.ts, iv)
        cl = self.premium(c, u.close, u.ts + tf, iv)
        # Premium is monotonic in spot: the CE peaks at the underlying high, the PE at the low.
        best, worst = (u.high, u.low) if c.option_type == OptionType.CE else (u.low, u.high)
        hi = max(o, cl, self.premium(c, best, mid_t, iv))
        lo = min(o, cl, self.premium(c, worst, mid_t, iv))
        return PremiumBar(o, hi, lo, cl)


class HybridPricer:
    """Stored candles where they exist for the entry, the model otherwise. One source per trade: a position is never priced by
    real candles on one bar and by Black-Scholes on the next, which would put artificial jumps into its path."""

    mode = "hybrid"

    def __init__(self, real: "RealPricer", synthetic: SyntheticPricer) -> None:
        self.real, self.synthetic = real, synthetic
        self.default_iv = synthetic.default_iv

    @property
    def missing_vix_bars(self) -> int:
        return self.synthetic.missing_vix_bars

    def contract(self, underlying: str, option_type: OptionType, spot: float, day: date,
                 lot_size: int | None = None) -> Contract | None:
        real = self.real.contract(underlying, option_type, spot, day, lot_size)
        if real is not None:
            return real
        m = self.synthetic.contract(underlying, option_type, spot, day, lot_size)  # no stored candles for this contract at all
        return Contract(m.underlying, m.expiry, m.strike, m.option_type, m.lot_size, True)

    def bar(self, c: Contract, u: Candle, tf: timedelta, vix: float | None) -> PremiumBar | None:
        return self.synthetic.bar(c, u, tf, vix) if c.synthetic else self.real.bar(c, u, tf, vix)

    def fallback(self, c: Contract) -> Contract | None:
        """The same contract priced by the model, for when the stored candles have no print at the entry bar."""
        return None if c.synthetic else Contract(c.underlying, c.expiry, c.strike, c.option_type, c.lot_size, True)


class RealPricer:
    """Option candles from the database, keyed by (expiry, strike, option_type) -> {bar ts: Candle}."""

    mode = "real"

    def __init__(self, candles: dict[tuple[date, float, OptionType], dict[datetime, Candle]],
                 lot_sizes: dict[tuple[date, float, OptionType], int], cal: TradingCalendar,
                 roll_on_expiry_day: bool = True) -> None:
        self.candles, self.lot_sizes, self.cal, self.roll = candles, lot_sizes, cal, roll_on_expiry_day
        self.missing_contracts: set[str] = set()
        self._expiries = sorted({k[0] for k in candles})

    def contract(self, underlying: str, option_type: OptionType, spot: float, day: date,
                 lot_size: int | None = None) -> Contract | None:
        live = [e for e in self._expiries if e > day or (e == day and not self.roll)]
        if not live:
            self.missing_contracts.add(f"{underlying} {day} no expiry with data")
            return None
        exp = live[0]
        strike = float(atm_strike(underlying, spot))
        key = (exp, strike, option_type)
        if key not in self.candles:
            self.missing_contracts.add(f"{underlying} {exp} {strike:g} {option_type.value}")
            return None
        return Contract(underlying, exp, strike, option_type, self.lot_sizes.get(key) or lot_size
                        or get_spec(underlying).lot_size)

    def bar(self, c: Contract, u: Candle, tf: timedelta, vix: float | None) -> PremiumBar | None:
        k = self.candles.get((c.expiry, c.strike, c.option_type), {}).get(u.ts)
        if k is None or not all(math.isfinite(x) for x in (k.open, k.high, k.low, k.close)):
            return None  # no trade printed in this bar
        return PremiumBar(k.open, k.high, k.low, k.close, k.volume)
